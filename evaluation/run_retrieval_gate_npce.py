"""Answer-grounded retrieval gate for C3/C4's actual retrieval mechanism
(shared retrieval core, NPCE-on). C3 and C4 share an identical retrieval
path (split -> remap -> metadata filter -> hybrid retrieve -> fuse ->
rerank; only the verifier differs between them), so one gate run
characterizes both -- there's no separate "C4 retrieval gate" to build.

Unlike `evaluation/run_retrieval_gate.py` (C1/C2's gate: one fixed,
deterministic query, no LLM call, whole-corpus search), this gate can't be
free or fully deterministic: it must run the real query splitter (an
actual LLM call) to get the same sub-queries C3/C4's own pipeline would
use, then apply the same metadata filter + hybrid retrieve + rerank per
sub-query C3/C4 actually run. Relevance is checked with
`evaluation.answer_grounded_metrics`'s number-boundary-matched verbatim
value presence, scoped to that sub-query's own metric (one value per
sub-query, not the whole question's gold_answers set the C1/C2 gate checks
against).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from contracts.interfaces import Embedder, Reranker
from contracts.models import PlaceholderRegistryEntry, RequestContext
from evaluation.answer_grounded_metrics import is_answer_present
from online.analysis.query_splitter import LLMQuerySplitter, render_subquery_question
from online.placeholders.remapper import remap_entity
from online.retrieval.fusion import apply_time_weighting, reciprocal_rank_fusion
from online.retrieval.hybrid import retrieve as hybrid_retrieve
from online.retrieval.metadata_filter import filter_candidates
from llm.client import LLMClient


@dataclass(frozen=True)
class SubQueryGateResult:
    collision_id: str
    sub_query_id: str
    metric: str
    gold_value: Optional[float]
    candidate_count: int
    hit_fused: float
    hit_reranked: float


def _gold_value_for_metric(gold_answers: dict, metric: str) -> Optional[float]:
    """SubQuery.metric is the same canonical identifier the collision
    record's gold_answers dict is keyed by (both come from the dataset's own
    metric_lexicon) -- exact key match, no fuzzy resolution needed."""
    return gold_answers.get(metric)


def evaluate_question(
    record: dict,
    bundle: object,
    embedder: Embedder,
    reranker: Reranker,
    registry: Sequence[PlaceholderRegistryEntry],
    splitter_client: LLMClient,
    top_k: int,
    rerank_top_k: int,
    time_weighting: bool,
) -> list[SubQueryGateResult]:
    ctx = RequestContext(request_id="gate", config_hash="gate")
    raw_sub_queries = LLMQuerySplitter(llm_client=splitter_client).transform(record["question"], ctx)
    sub_queries = [
        sq.model_copy(update={"entity": remap_entity(sq.entity, ctx, registry=registry)})
        for sq in raw_sub_queries
    ]

    results = []
    for sq in sub_queries:
        gold_value = _gold_value_for_metric(record.get("gold_answers", {}), sq.metric)
        gold_values = [gold_value] if gold_value is not None else []

        candidates = filter_candidates(sq, ctx, bundle.metadata_store)
        rendered_question = render_subquery_question(sq)
        scored = hybrid_retrieve(rendered_question, candidates, embedder)
        fused = reciprocal_rank_fusion(scored)
        fused = apply_time_weighting(fused, enabled=time_weighting)
        fused_props = [f.proposition for f in fused]

        reranked = list(reranker.rerank(sq, fused_props[:top_k], k=rerank_top_k)) if fused_props else []

        hit_fused = 1.0 if gold_values and any(is_answer_present(p, gold_values) for p in fused_props[:top_k]) else 0.0
        hit_reranked = 1.0 if gold_values and any(is_answer_present(p, gold_values) for p in reranked) else 0.0

        results.append(
            SubQueryGateResult(
                collision_id=record["collision_id"],
                sub_query_id=sq.sub_query_id,
                metric=sq.metric,
                gold_value=gold_value,
                candidate_count=len(candidates),
                hit_fused=hit_fused,
                hit_reranked=hit_reranked,
            )
        )
    return results


def run_gate(
    records: Sequence[dict],
    bundle: object,
    embedder: Embedder,
    reranker: Reranker,
    registry: Sequence[PlaceholderRegistryEntry],
    splitter_client: LLMClient,
    top_k: int = 20,
    rerank_top_k: int = 10,
    time_weighting: bool = True,
) -> dict[str, list[SubQueryGateResult]]:
    return {
        record["collision_id"]: evaluate_question(
            record, bundle, embedder, reranker, registry, splitter_client, top_k, rerank_top_k, time_weighting
        )
        for record in records
    }


def summarize(by_question: dict[str, list[SubQueryGateResult]]) -> dict:
    n_questions = len(by_question)
    any_hit_reranked = 0
    all_hit_reranked = 0
    any_hit_fused = 0
    all_sub_query_results: list[SubQueryGateResult] = []

    for collision_id, sub_results in by_question.items():
        scored = [r for r in sub_results if r.gold_value is not None]
        all_sub_query_results.extend(scored)
        if not scored:
            continue
        if any(r.hit_reranked for r in scored):
            any_hit_reranked += 1
        if all(r.hit_reranked for r in scored):
            all_hit_reranked += 1
        if any(r.hit_fused for r in scored):
            any_hit_fused += 1

    n_sub_queries = len(all_sub_query_results)
    return {
        "n_questions": n_questions,
        "n_sub_queries_scored": n_sub_queries,
        "any_sub_query_hit_rate_fused": any_hit_fused / n_questions if n_questions else None,
        "any_sub_query_hit_rate_reranked": any_hit_reranked / n_questions if n_questions else None,
        "all_sub_queries_hit_rate_reranked": all_hit_reranked / n_questions if n_questions else None,
        "per_sub_query_hit_rate_reranked": (
            sum(r.hit_reranked for r in all_sub_query_results) / n_sub_queries if n_sub_queries else None
        ),
    }
