"""Index writers — Path A & B convergence.

Writes vectors/postings/fields to dense, sparse (BM25), and metadata stores,
all keyed by proposition_id — what test_three_index_invariant checks.
These classes are in-memory stand-ins for tests; the real build path
(offline/indexing/build.py) persists to FAISS and Postgres instead, since
the index must survive unchanged across C0-C4 rather than get rebuilt.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Sequence, Union

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
        """Brute-force cosine similarity — fine at this scale, swap for
        FAISS/ANN behind this interface if the corpus grows."""
        scored = [(pid, cosine_similarity(query_vector, vec)) for pid, vec in self._vectors.items()]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]


_SPARSE_INDEX_FORMAT_VERSION = 1


class SparseIndexFormatError(ValueError):
    """A saved keyword index file is from a different build, or is corrupt."""


class SparseIndex:
    """Pure-Python BM25 (Robertson/Sparck-Jones), standard k1=1.5, b=0.75."""

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

    def remove(self, proposition_id: str) -> None:
        """Drops a ghost id -- one that exists in this index but no longer
        in the database. Silently a no-op if it was never here."""
        tokens = self._postings.pop(proposition_id, None)
        if tokens is None:
            return
        for term in set(tokens):
            self._doc_freq[term] -= 1

    def save(self, path: Union[str, Path]) -> None:
        """Saves the already-tokenized postings, so loading never re-reads
        or re-tokenizes the facts' text (step 1.3: nothing built at startup).
        Written via a temp file + rename so a reader never sees a half
        written file, even if another process is repairing at the same time."""
        data = {
            "version": _SPARSE_INDEX_FORMAT_VERSION,
            "k1": self._k1,
            "b": self._b,
            "postings": self._postings,
            "doc_freq": dict(self._doc_freq),
        }
        path = Path(path)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "SparseIndex":
        raw = Path(path).read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SparseIndexFormatError(f"{path} is not a valid keyword index file") from exc
        if data.get("version") != _SPARSE_INDEX_FORMAT_VERSION:
            raise SparseIndexFormatError(
                f"{path} is from a different build (version {data.get('version')!r}, "
                f"expected {_SPARSE_INDEX_FORMAT_VERSION}); rebuild it"
            )
        instance = cls(k1=data["k1"], b=data["b"])
        instance._postings = data["postings"]
        instance._doc_freq = Counter(data["doc_freq"])
        return instance


class MetadataStore:
    """In-memory stand-in for the real PostgreSQL/JSONB store. Only the
    identical-proposition_id-set invariant needs to survive the swap."""

    def __init__(self):
        self._propositions: dict[str, Proposition] = {}

    def upsert(self, proposition: Proposition) -> None:
        self._propositions[proposition.proposition_id] = proposition

    def ids(self) -> set[str]:
        return set(self._propositions.keys())

    def get(self, proposition_id: str) -> Proposition:
        return self._propositions[proposition_id]


class IndexWriters:
    """Index-writing orchestrator. Reuses the one shared Embedder (also
    used by offline/registry/placeholder_builder.py) — don't make a second."""

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
