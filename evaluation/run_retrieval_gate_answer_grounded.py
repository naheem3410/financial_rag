"""Answer-grounded retrieval gate for C1/C2's shared retrieval core
(unsplit query). Reuses the exact same retrieval path as
`evaluation/run_retrieval_gate.py` (`retrieve_fused` -> `CrossEncoderReranker`,
no split, no metadata filter -- C1/C2's actual mechanism) but scores with
`evaluation/answer_grounded_metrics.py`'s value-based relevance instead of
page-based: does the reranked window contain, verbatim, at least one
(any_answer_hit_rate) / all (all_answers_hit_rate) of the question's actual
gold answers, rather than "did the right page show up."
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Union

from contracts.interfaces import Embedder, Reranker
from contracts.models import RequestContext
from evaluation.answer_grounded_metrics import (
    all_answers_hit_rate_at_k,
    any_answer_hit_rate_at_k,
    answer_reciprocal_rank,
    gold_values_from_collision_record,
)
from evaluation.run_retrieval_gate import retrieve_fused
from online.analysis.query_splitter import PassthroughQueryTransformer


@dataclass(frozen=True)
class AnswerGroundedQuestionResult:
    collision_id: str
    question: str
    n_gold_values: int
    any_answer_hit_fused: float
    any_answer_hit_reranked: float
    all_answers_hit_reranked: float
    answer_reciprocal_rank_reranked: float


def evaluate_question(
    record: dict,
    bundle: object,
    embedder: Embedder,
    reranker: Reranker,
    top_k: int,
    rerank_top_k: int,
    time_weighting: bool,
) -> AnswerGroundedQuestionResult:
    gold_values = gold_values_from_collision_record(record)
    query_text = record["question"]

    fused = retrieve_fused(query_text, bundle, embedder, top_k=top_k, time_weighting=time_weighting)
    fused_props = [f.proposition for f in fused]

    ctx = RequestContext(request_id="gate", config_hash="gate")
    sub_query = PassthroughQueryTransformer().transform(query_text, ctx)[0]
    reranked = list(reranker.rerank(sub_query, fused_props[:top_k], k=rerank_top_k))

    return AnswerGroundedQuestionResult(
        collision_id=record["collision_id"],
        question=query_text,
        n_gold_values=len(gold_values),
        any_answer_hit_fused=any_answer_hit_rate_at_k(fused_props, gold_values, k=top_k),
        any_answer_hit_reranked=any_answer_hit_rate_at_k(reranked, gold_values, k=rerank_top_k),
        all_answers_hit_reranked=all_answers_hit_rate_at_k(reranked, gold_values, k=rerank_top_k),
        answer_reciprocal_rank_reranked=answer_reciprocal_rank(reranked, gold_values),
    )


def run_gate(
    records: Sequence[dict],
    bundle: object,
    embedder: Embedder,
    reranker: Reranker,
    top_k: int = 20,
    rerank_top_k: int = 10,
    time_weighting: bool = True,
) -> list[AnswerGroundedQuestionResult]:
    return [
        evaluate_question(record, bundle, embedder, reranker, top_k, rerank_top_k, time_weighting)
        for record in records
    ]


def summarize(results: Sequence[AnswerGroundedQuestionResult]) -> dict:
    n = len(results)
    if n == 0:
        return {}
    return {
        "n_questions": n,
        "any_answer_hit_rate_fused": sum(r.any_answer_hit_fused for r in results) / n,
        "any_answer_hit_rate_reranked": sum(r.any_answer_hit_reranked for r in results) / n,
        "all_answers_hit_rate_reranked": sum(r.all_answers_hit_reranked for r in results) / n,
        "answer_mrr_reranked": sum(r.answer_reciprocal_rank_reranked for r in results) / n,
    }
