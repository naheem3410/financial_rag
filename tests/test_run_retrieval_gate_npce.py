"""Tests for the C3/C4 answer-grounded retrieval gate's aggregation logic
(evaluation/run_retrieval_gate_npce.py). Retrieval/split/filter/rerank are
already tested elsewhere (test_metadata_filter.py, test_query_splitter.py,
test_answer_grounded_metrics.py) -- this only covers summarize()'s any/all
aggregation, the new logic this module adds.
"""
from __future__ import annotations

from evaluation.run_retrieval_gate_npce import SubQueryGateResult, summarize


def _result(cid, sqid, metric, gold_value, hit_reranked, hit_fused=None):
    return SubQueryGateResult(
        collision_id=cid, sub_query_id=sqid, metric=metric, gold_value=gold_value,
        candidate_count=10, hit_fused=hit_fused if hit_fused is not None else hit_reranked, hit_reranked=hit_reranked,
    )


def test_all_sub_queries_hit_counts_as_both_any_and_all():
    by_question = {
        "q1": [_result("q1", "sq_1", "accounts_payable", 2266, 1.0), _result("q1", "sq_2", "accounts_receivable", 5020, 1.0)],
    }
    summary = summarize(by_question)

    assert summary["any_sub_query_hit_rate_reranked"] == 1.0
    assert summary["all_sub_queries_hit_rate_reranked"] == 1.0


def test_partial_hit_counts_as_any_but_not_all():
    by_question = {
        "q1": [_result("q1", "sq_1", "accounts_payable", 2266, 1.0), _result("q1", "sq_2", "accounts_receivable", 5020, 0.0)],
    }
    summary = summarize(by_question)

    assert summary["any_sub_query_hit_rate_reranked"] == 1.0
    assert summary["all_sub_queries_hit_rate_reranked"] == 0.0


def test_no_hits_counts_as_neither():
    by_question = {
        "q1": [_result("q1", "sq_1", "accounts_payable", 2266, 0.0), _result("q1", "sq_2", "accounts_receivable", 5020, 0.0)],
    }
    summary = summarize(by_question)

    assert summary["any_sub_query_hit_rate_reranked"] == 0.0
    assert summary["all_sub_queries_hit_rate_reranked"] == 0.0


def test_sub_query_with_no_gold_value_is_excluded_from_scoring():
    """A sub-query whose metric has no matching gold_answers entry (e.g. an
    unmapped/mis-split metric) must not silently count as a miss or be
    scored at all."""
    by_question = {
        "q1": [_result("q1", "sq_1", "accounts_payable", 2266, 1.0), _result("q1", "sq_2", "some_unmapped_metric", None, 0.0)],
    }
    summary = summarize(by_question)

    assert summary["n_sub_queries_scored"] == 1  # the unscored one excluded
    assert summary["any_sub_query_hit_rate_reranked"] == 1.0
    assert summary["all_sub_queries_hit_rate_reranked"] == 1.0  # only the one real, hit sub-query counts


def test_per_sub_query_hit_rate_averages_across_all_scored_sub_queries_not_per_question():
    by_question = {
        "q1": [_result("q1", "sq_1", "accounts_payable", 2266, 1.0), _result("q1", "sq_2", "accounts_receivable", 5020, 1.0)],
        "q2": [_result("q2", "sq_1", "net_income", 5777, 0.0)],
    }
    summary = summarize(by_question)

    assert summary["n_sub_queries_scored"] == 3
    assert summary["per_sub_query_hit_rate_reranked"] == 2 / 3


def test_empty_input():
    assert summarize({}) == {
        "n_questions": 0, "n_sub_queries_scored": 0,
        "any_sub_query_hit_rate_fused": None, "any_sub_query_hit_rate_reranked": None,
        "all_sub_queries_hit_rate_reranked": None, "per_sub_query_hit_rate_reranked": None,
    }
