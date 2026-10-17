"""Retrieval-quality gate — Hit Rate / MRR / NDCG against real gold evidence.

Runs C1's retrieval core (hybrid + RRF + reranker, NPCE off) over an
already-built index. Should run before NPCE/CoVe since all cells share this
core. Uses the persisted dense/sparse indexes directly instead of
hybrid.retrieve's from-scratch re-embedding, since a passthrough query
touches the whole corpus (no metadata filter) and re-embedding all of it
per question would be wasteful. Reports fused-list and reranked-list
metrics separately since they isolate different stages.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Union

from contracts.interfaces import Embedder, Reranker
from contracts.models import Proposition, RequestContext
from evaluation.retrieval_metrics import (
    GoldEvidenceRef,
    gold_refs_from_collision_record,
    hit_rate_at_k,
    ndcg_at_k,
    reciprocal_rank,
)
from online.analysis.query_splitter import PassthroughQueryTransformer
from online.retrieval.fusion import FusedResult, apply_time_weighting, reciprocal_rank_fusion
from online.retrieval.hybrid import ScoredProposition

# Collision jsonl's `companies` shorthand -> real registrant name as
# indexed, per each filing's cover page (see filing_date.py docstring).
COMPANY_SHORTHAND_TO_REGISTRANT_NAME = {
    "3M": "3M Company",
    "Activision Blizzard": "Activision Blizzard, Inc.",
    "Adobe": "Adobe Systems Incorporated",
}


class _IndexBundleLike:
    """Structural stand-in for IndexBundle so test fakes don't need real FAISS/Postgres."""

    dense_index: object
    sparse_index: object
    metadata_store: object


@dataclass(frozen=True)
class QuestionResult:
    collision_id: str
    question: str
    gold_refs: list[GoldEvidenceRef]
    hit_rate_fused: float
    ndcg_fused: float
    hit_rate_reranked: float
    reciprocal_rank_reranked: float
    ndcg_reranked: float


def retrieve_fused(
    query_text: str, bundle: _IndexBundleLike, embedder: Embedder, top_k: int, time_weighting: bool
) -> list[FusedResult]:
    """Dense+sparse top-k over the persisted indexes, unioned and fused via real RRF."""
    [query_vector] = embedder.encode_queries([query_text])
    dense_hits = dict(bundle.dense_index.search(query_vector, k=top_k))
    sparse_hits = dict(bundle.sparse_index.search(query_text, k=top_k))
    # sorted(), not a bare set union: hash-seed randomization would otherwise
    # make tie-breaking in reciprocal_rank_fusion flip between process runs.
    candidate_ids = sorted(set(dense_hits) | set(sparse_hits))
    if not candidate_ids:
        return []

    scored = [
        ScoredProposition(
            proposition=bundle.metadata_store.get(pid),
            dense_score=dense_hits.get(pid, 0.0),
            sparse_score=sparse_hits.get(pid, 0.0),
        )
        for pid in candidate_ids
    ]
    fused = reciprocal_rank_fusion(scored)
    return apply_time_weighting(fused, enabled=time_weighting)


def evaluate_question(
    record: dict,
    bundle: _IndexBundleLike,
    embedder: Embedder,
    reranker: Reranker,
    top_k: int,
    rerank_top_k: int,
    time_weighting: bool,
) -> QuestionResult:
    """One question end to end: real retrieval + reranking, scored against
    gold evidence pages. Never reads gold_answers, only source refs."""
    gold_refs = gold_refs_from_collision_record(record)
    query_text = record["question"]

    fused = retrieve_fused(query_text, bundle, embedder, top_k=top_k, time_weighting=time_weighting)
    fused_props = [f.proposition for f in fused]

    sub_query = PassthroughQueryTransformer().transform(query_text, RequestContext(request_id="", config_hash=""))[0]
    reranked = list(reranker.rerank(sub_query, fused_props[:top_k], k=rerank_top_k))

    return QuestionResult(
        collision_id=record["collision_id"],
        question=query_text,
        gold_refs=gold_refs,
        hit_rate_fused=hit_rate_at_k(fused_props, gold_refs, k=top_k),
        ndcg_fused=ndcg_at_k(fused_props, gold_refs, k=top_k),
        hit_rate_reranked=hit_rate_at_k(reranked, gold_refs, k=rerank_top_k),
        reciprocal_rank_reranked=reciprocal_rank(reranked, gold_refs),
        ndcg_reranked=ndcg_at_k(reranked, gold_refs, k=rerank_top_k),
    )


def load_collision_records(path: Union[str, Path]) -> list[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def run_gate(
    records: Sequence[dict],
    bundle: _IndexBundleLike,
    embedder: Embedder,
    reranker: Reranker,
    top_k: int = 20,
    rerank_top_k: int = 5,
    time_weighting: bool = True,
) -> list[QuestionResult]:
    return [
        evaluate_question(record, bundle, embedder, reranker, top_k, rerank_top_k, time_weighting)
        for record in records
    ]


def summarize(results: Sequence[QuestionResult]) -> dict:
    n = len(results)
    if n == 0:
        return {}
    return {
        "n_questions": n,
        "hit_rate_at_top_k_fused": sum(r.hit_rate_fused for r in results) / n,
        "ndcg_at_top_k_fused": sum(r.ndcg_fused for r in results) / n,
        "hit_rate_at_rerank_k": sum(r.hit_rate_reranked for r in results) / n,
        "mrr_at_rerank_k": sum(r.reciprocal_rank_reranked for r in results) / n,
        "ndcg_at_rerank_k": sum(r.ndcg_reranked for r in results) / n,
    }
