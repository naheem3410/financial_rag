"""Ask-match signals and the deterministic ordering layers built on them.
No model is loaded here: scores are handed in directly."""
from __future__ import annotations

from typing import Optional

from config.schema import RetrievalConfig
from contracts.models import (
    Confidence, NumericRecord, Period, Proposition, PropositionMetadata, SubQuery,
)
from online.retrieval.ask_match import ask_match, drop_clear_mismatches, kind_signal, kind_tag, year_signal
from online.retrieval.reranker import (
    CrossEncoderReranker, RerankOptions, order_candidates, render_passage_text,
)


def _prop(pid: str, text: str, value: float, fy: int = 2020, metrics=(), scale: str = "millions",
          numeric: bool = True) -> Proposition:
    return Proposition(
        proposition_id=pid, text=text, provenance_tier="retrieved", ingestion_path="A_unstructured",
        numeric=NumericRecord(value=value, scale=scale, currency="USD", unit="absolute",
                              period=Period(fiscal_year=fy)) if numeric else None,
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="Lockheed Martin Corporation", filing_type="10-K",
                                     filing_date="2021-02-01", section="", page=1, source_uri="lmt.pdf"),
        metrics=tuple(metrics),
    )


def _sq(metric: str = "accounts_payable", fy: int = 2020) -> SubQuery:
    return SubQuery(sub_query_id="sq", entity="<ENT_1>", metric=metric,
                    period=Period(fiscal_year=fy), period_label=f"FY{fy}")


# The real Lockheed FY2020 shape: a cash-flow change line scored just above the balance.
FLOW = _prop("p_flow", "Lockheed Martin Corporation had changes in accounts payable of $(372) million "
                       "for the year ended December 31, 2020.", -372.0)
BALANCE = _prop("p_balance", "Lockheed Martin Corporation had accounts payable of $880 million at "
                             "December 31, 2020.", 880.0, metrics=("accounts_payable",))
NOISE = _prop("p_noise", "Lockheed Martin Corporation had stock-based compensation of $221 million "
                         "for the year ended December 31, 2020.", 221.0)


def test_kind_signal_flags_a_change_line_for_a_balance_metric():
    assert kind_signal(_sq(), FLOW) == -1
    assert kind_signal(_sq(), BALANCE) == 1
    # Any "for the year ended" fact is a period total, never a balance.
    assert kind_signal(_sq(), NOISE) == -1
    undated = _prop("p", "Lockheed Martin Corporation's goodwill balance was $10.8 billion.", 10.8)
    assert kind_signal(_sq(), undated) == 0


def test_kind_signal_only_applies_the_flow_rule_to_balance_metrics():
    flow_income = _prop("p", "Acme had net income of $5 million for the year ended December 31, 2020.",
                        5.0, metrics=("net_income",))
    assert kind_signal(_sq("net_income"), flow_income) == 1


def test_kind_signal_flags_a_value_that_is_just_the_year():
    junk = _prop("p", "Negative balances reclassified to accounts payable were $102 at December 31, 2020.",
                 2020.0, scale="unit", metrics=("accounts_payable",))
    assert kind_signal(_sq(), junk) == -1


def test_year_signal():
    assert year_signal(_sq(fy=2020), BALANCE) == 1
    assert year_signal(_sq(fy=2021), BALANCE) == -1
    assert year_signal(_sq(), _prop("p", "text", 1.0, numeric=False)) == 0


def test_ask_match_mismatched_property():
    assert ask_match(_sq(), FLOW).mismatched
    assert not ask_match(_sq(), BALANCE).mismatched
    assert ask_match(_sq(), NOISE).mismatched


def test_drop_clear_mismatches_keeps_order_and_never_empties():
    assert [p.proposition_id for p in drop_clear_mismatches(_sq(), [FLOW, NOISE, BALANCE])] == ["p_balance"]
    assert drop_clear_mismatches(_sq(), [FLOW]) == [FLOW]


def test_order_with_everything_off_is_the_plain_score_sort():
    scores = [0.9988, 0.9907, 0.9172]
    assert order_candidates(_sq(), [FLOW, BALANCE, NOISE], scores, RerankOptions()) == [0, 1, 2]
    assert order_candidates(_sq(), [NOISE, BALANCE, FLOW], [0.1, 0.5, 0.9], RerankOptions()) == [2, 1, 0]


def test_tie_break_promotes_the_balance_over_a_close_flow_line():
    # Real scores: 0.9988 vs 0.9907 is about 2.5 apart in logit space.
    order = order_candidates(_sq(), [FLOW, BALANCE, NOISE], [0.9988, 0.9907, 0.9172],
                             RerankOptions(tie_break_logit_margin=3.0))
    assert order[0] == 1


def test_tie_break_leaves_a_clear_score_gap_alone():
    order = order_candidates(_sq(), [FLOW, BALANCE], [0.9988, 0.30],
                             RerankOptions(tie_break_logit_margin=3.0))
    assert order == [0, 1]


def test_blend_moves_the_balance_up_by_its_signals():
    order = order_candidates(_sq(), [FLOW, BALANCE], [0.9988, 0.9907],
                             RerankOptions(blend_kind_weight=2.0, blend_year_weight=0.0))
    assert order == [1, 0]


def test_passage_tag_names_the_kind_of_fact_only():
    assert render_passage_text(BALANCE) == "[balance at period end] " + BALANCE.text
    assert kind_tag(FLOW) == "change during the period"
    assert render_passage_text(_prop("p", "Plain text.", 1.0, numeric=False)) == "Plain text."


def test_config_defaults_leave_every_layer_off():
    retrieval = RetrievalConfig()
    assert RerankOptions.from_retrieval_config(retrieval) == RerankOptions()
    assert retrieval.enable_ask_match_guard is False
    assert CrossEncoderReranker.from_config(retrieval).options == RerankOptions()


def test_config_rejects_a_negative_margin():
    import pytest
    with pytest.raises(ValueError):
        RetrievalConfig(rerank_tie_break_logit_margin=-1.0)


class _FakeModel:
    def __init__(self, scores):
        self._scores = scores
        self.pairs = []

    def predict(self, pairs):
        self.pairs = list(pairs)
        return self._scores


def test_rerank_scored_pairs_each_fact_with_its_logit_in_rerank_order():
    reranker = CrossEncoderReranker(options=RerankOptions(tie_break_logit_margin=3.0))
    reranker._model = _FakeModel([0.9988, 0.9907, 0.9172])

    scored = reranker.rerank_scored(_sq(), [FLOW, BALANCE, NOISE], k=2)

    assert [p.proposition_id for p, _ in scored] == ["p_balance", "p_flow"]
    assert abs(scored[0][1] - 4.67) < 0.01
    assert [p.proposition_id for p in reranker.rerank(_sq(), [FLOW, BALANCE, NOISE], k=2)] == ["p_balance", "p_flow"]


def test_tagging_changes_only_what_the_model_reads():
    reranker = CrossEncoderReranker(options=RerankOptions(tag_passages=True))
    model = _FakeModel([0.5, 0.6])
    reranker._model = model

    result = reranker.rerank(_sq(), [FLOW, BALANCE], k=2)

    assert model.pairs[1][1] == "[balance at period end] " + BALANCE.text
    assert result[0].text == BALANCE.text
