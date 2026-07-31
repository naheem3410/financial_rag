"""Retrieval-quality gate — Hit Rate / MRR / NDCG against real gold
evidence.

Runs C1's retrieval core (hybrid + RRF fusion + reranker, NPCE off) as a
real query pipeline over an already-built index, and scores it against
`evaluation/retrieval_metrics.py`'s Hit Rate/MRR/NDCG — the gate that
should run before layering NPCE (C3) / CoVe (C2) on top, since all cells
share an identical retrieval core and that core needs to be validated on
its own before anything else is measured against it.

Dense arm uses FAISS's persisted index, not
`online/retrieval/hybrid.py:retrieve`'s from-scratch re-embedding.
`hybrid.retrieve` re-embeds every candidate on every call — correct and
fine at the metadata-filtered candidate-set scale it was built for (a
handful to a few hundred propositions), but C1's own metadata filter is a
no-op for NPCE-off passthrough queries, so a literal C1 query hits the
entire corpus every time (tens of thousands of propositions here).
Re-embedding all of them per query, once per question, is the kind of cost
the persisted `PersistentDenseIndex`
(`offline/indexing/dense_index_faiss.py`) exists specifically to avoid.
This module uses the persisted dense index and the persisted BM25 sparse
index for their respective top-k searches, unions the result ids, and only
then builds `ScoredProposition`s and hands them to the real, tested
`online.retrieval.fusion.reciprocal_rank_fusion` — reusing the exact
fusion math, just skipping needless re-embedding to get there.

Reranking uses the real `CrossEncoderReranker` (a local cross-encoder, not
an LLM call — no API cost) via `PassthroughQueryTransformer`'s `SubQuery`
shape, matching what C1 (NPCE-off) actually constructs.

Reports two views per question: Hit Rate/NDCG@`top_k` on the fused list
(did the right evidence even survive fusion) and Hit Rate/MRR/NDCG@
`rerank_top_k` on the reranked list (what the generator would actually
see) — both matter: the first isolates fusion/retrieval recall, the
second is what C1 actually serves downstream.
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

# Company shorthand used in the Collision jsonl's `companies` field ->
# the real registrant name each proposition is indexed under, sourced
# from each filing's own cover page -- see
# offline/parsers/filing_date.py's module docstring for why the stated
# name/date is used rather than a naming-convention guess.
COMPANY_SHORTHAND_TO_REGISTRANT_NAME = {
    "3M": "3M Company",
    "Activision Blizzard": "Activision Blizzard, Inc.",
    "Adobe": "Adobe Systems Incorporated",
}


class _IndexBundleLike:
    """Structural type for what this module needs from an IndexBundle
    (offline.indexing.build.IndexBundle) — documented here rather than
    imported, so a fake bundle in tests doesn't need real FAISS/Postgres."""

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
    """Dense (FAISS) + sparse (BM25) top-k search over the persisted index,
    unioned and fused via the real RRF — see module docstring for why this
    doesn't call `hybrid.retrieve` directly."""
    [query_vector] = embedder.encode_queries([query_text])
    dense_hits = dict(bundle.dense_index.search(query_vector, k=top_k))
    sparse_hits = dict(bundle.sparse_index.search(query_text, k=top_k))
    # sorted(), not a bare set union: Python randomizes string hash seeds
    # per process (PYTHONHASHSEED unset by default), so
    # `set(dense_hits) | set(sparse_hits)` iterates in a different order
    # every process invocation. reciprocal_rank_fusion's rank assignment
    # uses a stable sort on dense_score/sparse_score, so any tie in either
    # score resolves according to this input order -- an unsorted union
    # would make reranked hit/miss flip between process runs on the exact
    # same index. Sorting gives a fixed, reproducible candidate order
    # regardless of hash seed.
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
    """One question, end to end: real retrieval (dense+sparse+RRF), real
    reranking, scored against this question's real gold evidence pages.
    Never reads `gold_answers` — only `source` (doc/page refs) and
    `question` (the query text)."""
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
