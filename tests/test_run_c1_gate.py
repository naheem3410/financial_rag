"""C1 gate scoring tests (decided 2026-07-25). Must PASS. Covers the
scale-detection and exact-match logic in isolation — the pipeline wiring
itself is covered by tests/test_pipeline_c1.py.
"""
from __future__ import annotations

from decimal import Decimal

from evaluation.run_c1_gate import (
    detect_scale_multiplier,
    gold_values_in_base_units,
    is_exact_match,
    generated_values_in_base_units,
)
from online.output.generator import AnswerValue
from online.pipeline_c1 import C1AnswerResult


def _make_result(abstained=False, answerable=True, answers=None) -> C1AnswerResult:
    return C1AnswerResult(
        question="q", abstained=abstained, abstention_message="", answerable=answerable,
        answers=answers or [], answer_text="", citations=[], used_fff_ids=[],
        retrieved_page_count=1, fff_count=1, fff_rewriter_model="m", generator_model="m",
    )


def test_detect_scale_multiplier_million():
    assert detect_scale_multiplier("$32,765 million") == Decimal(1_000_000)


def test_detect_scale_multiplier_billion():
    assert detect_scale_multiplier("$1.5 billion") == Decimal(1_000_000_000)


def test_detect_scale_multiplier_plural_form():
    assert detect_scale_multiplier("14,688 millions") == Decimal(1_000_000)


def test_detect_scale_multiplier_no_scale_word_defaults_to_unit():
    assert detect_scale_multiplier("18.2 percent") == Decimal(1)


def test_gold_values_in_base_units_applies_row_scale():
    gold = {"accounts_receivable": 4532, "total_current_assets": 14688}
    values = gold_values_in_base_units(gold, "millions")
    assert values == {Decimal("4532000000"), Decimal("14688000000")}


def test_gold_values_in_base_units_skips_null_entries():
    gold = {"accounts_receivable": 4532, "total_current_assets": None}
    values = gold_values_in_base_units(gold, "millions")
    assert values == {Decimal("4532000000")}


def test_exact_match_true_when_both_metrics_reproduced():
    result = _make_result(answers=[
        AnswerValue(metric="accounts_receivable", value="$4,532 million", unit="millions"),
        AnswerValue(metric="total_current_assets", value="$14,688 million", unit="millions"),
    ])
    gold = {"accounts_receivable": 4532, "total_current_assets": 14688}

    assert is_exact_match(result, gold, "millions") is True


def test_exact_match_false_when_one_metric_is_wrong():
    result = _make_result(answers=[
        AnswerValue(metric="accounts_receivable", value="$4,532 million", unit="millions"),
        AnswerValue(metric="total_current_assets", value="$99,999 million", unit="millions"),
    ])
    gold = {"accounts_receivable": 4532, "total_current_assets": 14688}

    assert is_exact_match(result, gold, "millions") is False


def test_exact_match_false_when_abstained():
    result = _make_result(abstained=True, answerable=False, answers=[])
    gold = {"accounts_receivable": 4532, "total_current_assets": 14688}

    assert is_exact_match(result, gold, "millions") is False


def test_exact_match_ignores_metric_label_naming_and_matches_by_value():
    """The Generator's metric label is free text, not a controlled
    vocabulary -- a differently-phrased label with the right value must
    still count as a match."""
    result = _make_result(answers=[
        AnswerValue(metric="net accounts receivable balance", value="$4,532 million", unit="millions"),
        AnswerValue(metric="total current assets, consolidated", value="$14,688 million", unit="millions"),
    ])
    gold = {"accounts_receivable": 4532, "total_current_assets": 14688}

    assert is_exact_match(result, gold, "millions") is True


def test_exact_match_false_when_only_one_of_two_values_present():
    result = _make_result(answers=[
        AnswerValue(metric="accounts_receivable", value="$4,532 million", unit="millions"),
    ])
    gold = {"accounts_receivable": 4532, "total_current_assets": 14688}

    assert is_exact_match(result, gold, "millions") is False


def test_generated_values_handles_mixed_scale_within_one_answer_set():
    result_values = generated_values_in_base_units([
        AnswerValue(metric="a", value="$1.5 billion", unit="billions"),
        AnswerValue(metric="b", value="$500 million", unit="millions"),
    ])
    assert result_values == {Decimal("1500000000"), Decimal("500000000")}


def test_generated_values_falls_back_to_unit_field_when_value_has_no_scale_word():
    """Real bug, found 2026-07-26 re-scoring a re-index's C1 answers: the
    Generator sometimes reports a correct figure as value="13,709",
    unit="million" instead of embedding the scale word in `value`. Both
    shapes are valid per AnswerValue's own schema -- only checking `a.value`
    silently mis-scored the second shape as a different (wrong) number."""
    result_values = generated_values_in_base_units([
        AnswerValue(metric="a", value="13,709", unit="million"),
    ])
    assert result_values == {Decimal("13709000000")}


def test_exact_match_true_when_scale_word_is_in_unit_field_not_value():
    result = _make_result(answers=[
        AnswerValue(metric="total_current_assets", value="13,709", unit="million"),
    ])
    gold = {"total_current_assets": 13709}

    assert is_exact_match(result, gold, "millions") is True
