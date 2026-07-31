"""Hybrid retriever.

BM25 arm for exact ticker/line-item/identifier match; dense arm for
semantic recall — both run over the metadata-filtered candidate set, not
the full corpus. Uses the one shared `Embedder`: the query goes through
`encode_queries` (BGE's asymmetric prefix), candidate/passage text through
`encode_passages` — never the reverse.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from contracts.interfaces import Embedder
from contracts.models import Proposition
from contracts.vectors import cosine_similarity
from offline.indexing.index_writers import SparseIndex


@dataclass(frozen=True)
class ScoredProposition:
    proposition: Proposition
    dense_score: float
    sparse_score: float


def retrieve(
    query_text: str, candidates: Sequence[Proposition], embedder: Embedder
) -> list[ScoredProposition]:
    """Scores every candidate on both arms; does not itself rank or fuse
    them — that's rank fusion's job (online/retrieval/fusion.py)."""
    if not candidates:
        return []

    [query_vector] = embedder.encode_queries([query_text])
    passage_vectors = embedder.encode_passages([c.text for c in candidates])
    dense_scores = {
        c.proposition_id: cosine_similarity(query_vector, vector)
        for c, vector in zip(candidates, passage_vectors)
    }

    sparse_index = SparseIndex()
    for candidate in candidates:
        sparse_index.upsert(candidate.proposition_id, candidate.text)
    sparse_results = dict(sparse_index.search(query_text, k=len(candidates)))
    # Every candidate gets an explicit score, 0.0 if it shares no BM25 term
    # with the query — `search` only returns documents with nonzero score.
    sparse_scores = {c.proposition_id: sparse_results.get(c.proposition_id, 0.0) for c in candidates}

    return [
        ScoredProposition(
            proposition=c,
            dense_score=dense_scores[c.proposition_id],
            sparse_score=sparse_scores[c.proposition_id],
        )
        for c in candidates
    ]
