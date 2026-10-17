"""Hybrid retriever: BM25 arm for exact ticker/line-item match, dense arm
for semantic recall, both over the metadata-filtered candidates only.
Query side uses encode_queries, passages use encode_passages — never swap them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from contracts.interfaces import Embedder
from contracts.models import Proposition
from contracts.vectors import cosine_similarity
from offline.indexing.dense_index_faiss import PersistentDenseIndex
from offline.indexing.index_writers import SparseIndex

# If more than this share of candidates has no saved vector, something is
# wrong with the index (not just a fact or two not caught up yet) -- refuse
# rather than answer from a badly incomplete search.
MAX_MISSING_VECTOR_SHARE = 0.5


class TooManyVectorsMissing(RuntimeError):
    """More than half the candidates have no saved vector; the index likely
    needs repair (see the startup check, step 1.3), not a one-off skip."""

    def __init__(self, missing_ids: list[str], total: int):
        self.missing_ids = missing_ids
        super().__init__(
            f"{len(missing_ids)} of {total} candidates have no saved vector "
            f"(over {int(MAX_MISSING_VECTOR_SHARE * 100)}%); refusing to search on an incomplete index"
        )


@dataclass(frozen=True)
class ScoredProposition:
    proposition: Proposition
    dense_score: float
    sparse_score: float


def retrieve(
    query_text: str,
    candidates: Sequence[Proposition],
    embedder: Embedder,
    dense_index: Optional[PersistentDenseIndex] = None,
) -> list[ScoredProposition]:
    """Scores every candidate on both arms; ranking/fusion happens
    elsewhere (online/retrieval/fusion.py).

    `dense_index=None` (the old path) re-embeds every candidate's text on
    every call -- correct but slow. Passing the same `dense_index` the
    candidates came from reads their vectors back instead of re-embedding
    them (known issue 2/8, decisions 3/13). A candidate missing from the
    index is dropped rather than scored wrong; too many missing raises
    `TooManyVectorsMissing` instead of guessing."""
    if not candidates:
        return []

    [query_vector] = embedder.encode_queries([query_text])

    if dense_index is None:
        passage_vectors = embedder.encode_passages([c.text for c in candidates])
        vectors_by_id = {c.proposition_id: v for c, v in zip(candidates, passage_vectors)}
    else:
        vectors_by_id, missing = dense_index.get_vectors([c.proposition_id for c in candidates])
        if missing and len(missing) / len(candidates) > MAX_MISSING_VECTOR_SHARE:
            raise TooManyVectorsMissing(missing, len(candidates))
        candidates = [c for c in candidates if c.proposition_id in vectors_by_id]
        if not candidates:
            return []

    dense_scores = {
        proposition_id: cosine_similarity(query_vector, vector)
        for proposition_id, vector in vectors_by_id.items()
    }

    sparse_index = SparseIndex()
    for candidate in candidates:
        sparse_index.upsert(candidate.proposition_id, candidate.text)
    sparse_results = dict(sparse_index.search(query_text, k=len(candidates)))
    # backfill 0.0 for candidates search() skipped (nonzero-score only)
    sparse_scores = {c.proposition_id: sparse_results.get(c.proposition_id, 0.0) for c in candidates}

    return [
        ScoredProposition(
            proposition=c,
            dense_score=dense_scores[c.proposition_id],
            sparse_score=sparse_scores[c.proposition_id],
        )
        for c in candidates
    ]
