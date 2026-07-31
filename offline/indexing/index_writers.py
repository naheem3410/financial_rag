"""Index writers — Path A & B convergence.

Engine: deterministic code + the one shared embedding model
(`Embedder`, contracts/interfaces.py — see offline/embedding/bge_embedder.py
for why exactly one embedding path is used across the whole system). Writes
vectors -> dense index, postings -> sparse index (BM25), structured fields
-> metadata store, all keyed by the shared `proposition_id`. This is what
`test_three_index_invariant` actually checks: all three stores must contain
an identical `proposition_id` set.

In-memory vs. persistent: this module's `DenseIndex`/`SparseIndex`/
`MetadataStore` are lightweight in-memory stores — useful for tests and for
anything that doesn't need to survive past one process. For the real build
(the index must be built once and reused unchanged across C0-C4, since
rebuilding would re-invoke the LLM-based proposition segmenter), use
`offline/indexing/build.py` instead, which persists to FAISS
(`dense_index_faiss.py`) and PostgreSQL/JSONB (`metadata_store_postgres.py`)
— both backed by the shared `Embedder`, same as here. `DenseIndex` here
does brute-force cosine similarity (`contracts/vectors.py`), adequate at
this corpus's scale. `SparseIndex` implements real BM25 scoring
(Robertson/Sparck-Jones, k1=1.5, b=0.75) in pure Python rather than a stub,
and is reused as-is by the persistent path (sparse postings are cheap to
rebuild deterministically and don't need their own persistence). The only
invariant that must survive either backend is the three-index one above.

Dimension consistency (mandatory): `DenseIndex.upsert` refuses (raises
`ValueError`) to write a vector whose length doesn't match the index's
declared `dimension` — writing a corrupt index silently is worse than
crashing loudly. `IndexWriters` always constructs its `DenseIndex` from
`embedder.dimension`, so normal usage can never mismatch; the check exists
for whoever writes vectors from a different source later.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Sequence

from contracts.interfaces import Embedder
from contracts.models import Proposition
from contracts.vectors import cosine_similarity

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class DenseIndex:
    def __init__(self, dimension: int):
        self.dimension = dimension
        self._vectors: dict[str, list[float]] = {}

    def upsert(self, proposition_id: str, vector: Sequence[float]) -> None:
        if len(vector) != self.dimension:
            raise ValueError(
                f"DenseIndex dimension mismatch: index expects {self.dimension}-d "
                f"vectors, got {len(vector)}-d for proposition_id={proposition_id!r}. "
                "Refusing to write a corrupt index."
            )
        self._vectors[proposition_id] = list(vector)

    def ids(self) -> set[str]:
        return set(self._vectors.keys())

    def search(self, query_vector: Sequence[float], k: int) -> list[tuple[str, float]]:
        """Brute-force cosine similarity — adequate at this corpus's scale;
        swap for FAISS/an ANN store behind this same interface if the
        corpus ever grows past what brute force can serve."""
        scored = [(pid, cosine_similarity(query_vector, vec)) for pid, vec in self._vectors.items()]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]


class SparseIndex:
    """Pure-Python BM25 (Robertson/Sparck-Jones), k1=1.5, b=0.75 — the
    standard defaults."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._postings: dict[str, list[str]] = {}
        self._doc_freq: Counter = Counter()

    def upsert(self, proposition_id: str, text: str) -> None:
        if proposition_id in self._postings:
            for term in set(self._postings[proposition_id]):
                self._doc_freq[term] -= 1
        tokens = _tokenize(text)
        self._postings[proposition_id] = tokens
        for term in set(tokens):
            self._doc_freq[term] += 1

    def ids(self) -> set[str]:
        return set(self._postings.keys())

    def _avg_doc_length(self) -> float:
        if not self._postings:
            return 0.0
        return sum(len(toks) for toks in self._postings.values()) / len(self._postings)

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        query_terms = _tokenize(query)
        n_docs = len(self._postings)
        avg_len = self._avg_doc_length()
        scores: dict[str, float] = dict.fromkeys(self._postings, 0.0)

        for term in query_terms:
            df = self._doc_freq.get(term, 0)
            if df == 0:
                continue
            idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)
            for pid, tokens in self._postings.items():
                tf = tokens.count(term)
                if tf == 0:
                    continue
                doc_len = len(tokens)
                denom = tf + self._k1 * (1 - self._b + self._b * doc_len / avg_len)
                scores[pid] += idf * (tf * (self._k1 + 1)) / denom

        ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
        return ranked[:k]


class MetadataStore:
    """In-memory stand-in for the eventual PostgreSQL/JSONB store — no
    Postgres instance is configured in this environment. Swapping this for
    a real one is a separate infrastructure decision; the only contract
    that must survive the swap is the identical-`proposition_id`-set
    invariant across all three stores."""

    def __init__(self):
        self._propositions: dict[str, Proposition] = {}

    def upsert(self, proposition: Proposition) -> None:
        self._propositions[proposition.proposition_id] = proposition

    def ids(self) -> set[str]:
        return set(self._propositions.keys())

    def get(self, proposition_id: str) -> Proposition:
        return self._propositions[proposition_id]


class IndexWriters:
    """Index-writing orchestrator. `embedder` is the one shared `Embedder`
    (contracts/interfaces.py) also used by the placeholder registry builder
    (offline/registry/placeholder_builder.py) — never construct a second
    one."""

    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.dense_index = DenseIndex(dimension=embedder.dimension)
        self.sparse_index = SparseIndex()
        self.metadata_store = MetadataStore()

    def write(self, propositions: Sequence[Proposition]) -> None:
        if not propositions:
            return
        vectors = self.embedder.encode_passages([p.text for p in propositions])
        for proposition, vector in zip(propositions, vectors):
            self.dense_index.upsert(proposition.proposition_id, vector)
            self.sparse_index.upsert(proposition.proposition_id, proposition.text)
            self.metadata_store.upsert(proposition)
