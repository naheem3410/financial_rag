"""Answer-grounded retrieval metric tests (evaluation/answer_grounded_metrics.py)."""
from __future__ import annotations

from contracts.models import Confidence, Proposition, PropositionMetadata
from evaluation.answer_grounded_metrics import (
    all_answers_hit_rate_at_k,
    any_answer_hit_rate_at_k,
    answer_reciprocal_rank,
    gold_values_from_collision_record,
    is_answer_present,
)


def _prop(pid: str, text: str) -> Proposition:
    return Proposition(
        proposition_id=pid,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="3M Company", filing_type="10-K", filing_date="2018-12-31",
            section="", page=57, source_uri="3M_2018_10K",
        ),
    )


def test_is_answer_present_exact_match():
    p = _prop("p_1", "3M Company had accounts payable of $2,266 million at December 31, 2018.")
    assert is_answer_present(p, [2266]) is True


def test_is_answer_present_number_boundary_no_false_positive():
    """"3,231" must not match inside "13,231" -- the exact false-positive
    risk found and fixed during the real 18-case addressability check."""
    p = _prop("p_1", "3M Company had total assets of $13,231 million at December 31, 2018.")
    assert is_answer_present(p, [3231]) is False


def test_is_answer_present_false_when_no_value_matches():
    p = _prop("p_1", "3M Company had inventories of $4,366 million at December 31, 2018.")
    assert is_answer_present(p, [2266, 5020]) is False


def test_any_answer_hit_rate_true_when_one_of_two_values_present():
    props = [
        _prop("p_1", "3M Company had inventories of $4,366 million at December 31, 2018."),
        _prop("p_2", "3M Company had accounts payable of $2,266 million at December 31, 2018."),
    ]
    assert any_answer_hit_rate_at_k(props, gold_values=[2266, 5020], k=10) == 1.0


def test_any_answer_hit_rate_zero_when_neither_value_present():
    props = [_prop("p_1", "3M Company had inventories of $4,366 million at December 31, 2018.")]
    assert any_answer_hit_rate_at_k(props, gold_values=[2266, 5020], k=10) == 0.0


def test_any_answer_hit_rate_respects_k_cutoff():
    props = [
        _prop("p_1", "unrelated"),
        _prop("p_2", "3M Company had accounts payable of $2,266 million at December 31, 2018."),
    ]
    assert any_answer_hit_rate_at_k(props, gold_values=[2266], k=1) == 0.0
    assert any_answer_hit_rate_at_k(props, gold_values=[2266], k=2) == 1.0


def test_all_answers_hit_rate_requires_every_value_present():
    props = [
        _prop("p_1", "3M Company had accounts payable of $2,266 million at December 31, 2018."),
        _prop("p_2", "3M Company had accounts receivable of $5,020 million at December 31, 2018."),
    ]
    assert all_answers_hit_rate_at_k(props, gold_values=[2266, 5020], k=10) == 1.0


def test_all_answers_hit_rate_false_when_only_one_of_two_present():
    props = [_prop("p_1", "3M Company had accounts payable of $2,266 million at December 31, 2018.")]
    assert all_answers_hit_rate_at_k(props, gold_values=[2266, 5020], k=10) == 0.0


def test_answer_reciprocal_rank():
    props = [
        _prop("p_1", "unrelated fact"),
        _prop("p_2", "3M Company had accounts payable of $2,266 million at December 31, 2018."),
        _prop("p_3", "3M Company had accounts receivable of $5,020 million at December 31, 2018."),
    ]
    assert answer_reciprocal_rank(props, gold_values=[2266, 5020]) == 0.5  # rank 2


def test_answer_reciprocal_rank_zero_when_absent():
    props = [_prop("p_1", "unrelated fact")]
    assert answer_reciprocal_rank(props, gold_values=[2266]) == 0.0


def test_gold_values_from_collision_record_skips_nulls():
    record = {"gold_answers": {"accounts_payable": 2266, "accounts_receivable": None}}
    assert gold_values_from_collision_record(record) == [2266]
