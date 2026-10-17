"""offline/labeling/metric_labeler.py: rule-based metric labels, plus the
model fallback for facts the rules found nothing on. Step 1.4."""
from __future__ import annotations

from offline.labeling.metric_labeler import (
    DEFAULT_METRIC_ALIASES,
    label_metrics,
    label_metrics_llm_fallback,
    label_metrics_with_fallback,
)


class _SequencedClient:
    """Same fake used across the pipeline tests: returns each queued
    response in order, no real model call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def test_a_clear_metric_is_labelled():
    text = "3M Company had net income of $5,777 million for the year ended December 31, 2022."

    assert label_metrics(text) == ("net_income",)


def test_a_fact_with_two_metrics_gets_both():
    text = "Operating income was $8.9 billion and net income was $5.7 billion."

    assert label_metrics(text) == ("net_income", "operating_income")


def test_a_fact_nobody_can_label_stays_unlabelled():
    text = "The company relocated its headquarters to a new office building."

    assert label_metrics(text) == ()


def test_case_is_ignored():
    text = "NET INCOME for the quarter was $100 million."

    assert label_metrics(text) == ("net_income",)


def test_matches_a_whole_phrase_not_a_substring():
    """"net income" must not fire on unrelated text that merely contains
    the word "income" or "net" separately."""
    text = "Net cash used in operating activities was $12 million; taxable income rose."

    assert label_metrics(text) == ()


def test_total_net_sales_maps_to_total_revenue():
    text = "Total net sales for fiscal 2018 were $32,765 million."

    assert label_metrics(text) == ("total_revenue",)


def test_bare_net_sales_also_maps_to_total_revenue():
    text = "Net sales increased 5 percent year over year."

    assert label_metrics(text) == ("total_revenue",)


def test_plain_revenue_is_its_own_label_distinct_from_total_revenue():
    text = "Revenue from the segment grew steadily."

    assert label_metrics(text) == ("revenue",)


def test_diluted_and_basic_eps_are_distinguished():
    assert label_metrics("Diluted earnings per share was $2.15.") == ("diluted_eps",)
    assert label_metrics("Basic earnings per share was $2.20.") == ("basic_eps",)


def test_capex_abbreviation_is_recognized():
    text = "Capex for the year totaled $1.2 billion."

    assert label_metrics(text) == ("capital_expenditure",)


def test_non_gaap_text_also_matches_plain_gaap_a_known_rule_based_limit():
    """"non-GAAP" genuinely contains the word "GAAP", so both fire. A
    documented limit of literal phrase matching, not a bug -- distinguishing
    them needs a smarter rule the bigger dictionary (2.2) can carry."""
    text = "This is a non-GAAP measure."

    assert label_metrics(text) == ("gaap", "non_gaap")


def test_empty_text_is_unlabelled():
    assert label_metrics("") == ()


def test_custom_alias_dictionary_can_be_supplied():
    """The seam work package 2.2's bigger dictionary plugs into."""
    custom = {"widget_count": ("widgets sold",)}

    assert label_metrics("We sold 100 widgets sold last year.", aliases=custom) == ("widget_count",)


# --- step 2.2's extra aliases (2026-10-17): standard financial English,
# British/Nigerian usage included, owner-reviewed before being seeded ---


def test_turnover_maps_to_total_revenue():
    text = "Group turnover for the year was 32,765 million."

    assert label_metrics(text) == ("total_revenue",)


def test_topline_and_total_sales_also_map_to_total_revenue():
    assert label_metrics("Topline grew 5 percent year over year.") == ("total_revenue",)
    assert label_metrics("Total sales increased in the quarter.") == ("total_revenue",)


def test_sales_revenue_is_deliberately_not_an_alias():
    """Dropped on purpose: "sales revenue" literally contains "revenue",
    so it would have silently double-labelled as revenue too."""
    text = "Sales revenue increased in the quarter."

    assert label_metrics(text) == ("revenue",)


def test_profit_after_tax_and_bottom_line_map_to_net_income():
    assert label_metrics("Profit after tax was $5,777 million.") == ("net_income",)
    assert label_metrics("The bottom line improved this quarter.") == ("net_income",)


def test_net_profit_margin_is_not_mislabelled_as_net_income():
    """"net profit" alone was dropped from net_income's aliases on
    purpose: it is a literal substring of "net profit margin", so keeping
    it would have double-labelled every net_profit_margin sentence."""
    text = "Net profit margin improved to 12 percent."

    assert label_metrics(text) == ("net_profit_margin",)


def test_operating_profit_maps_to_operating_income():
    text = "Operating profit for the segment was $8.9 billion."

    assert label_metrics(text) == ("operating_income",)


def test_trade_payables_and_trade_creditors_map_to_accounts_payable():
    assert label_metrics("Trade payables increased during the period.") == ("accounts_payable",)
    assert label_metrics("Trade creditors were settled within terms.") == ("accounts_payable",)


def test_trade_receivables_and_trade_debtors_map_to_accounts_receivable():
    assert label_metrics("Trade receivables grew in line with sales.") == ("accounts_receivable",)
    assert label_metrics("Trade debtors remained within the credit policy.") == ("accounts_receivable",)


def test_capital_spending_maps_to_capital_expenditure():
    text = "Capital spending for the year totaled $1.2 billion."

    assert label_metrics(text) == ("capital_expenditure",)


def test_stock_in_trade_maps_to_inventories():
    text = "Stock-in-trade at year end was valued at $40 million."

    assert label_metrics(text) == ("inventories",)


def test_bare_stock_is_deliberately_not_an_inventories_alias():
    """"stock" alone was left out on purpose -- it collides with equity
    stock, so a sentence about shares must never be labelled inventories."""
    text = "Common stock outstanding was 500 million shares."

    assert label_metrics(text) == ()


def test_gross_profit_margin_is_not_mislabelled_as_gross_profit():
    """"gross profit margin" was deliberately not added as a gross_margin
    alias: it literally contains "gross profit", which would have
    double-labelled it as gross_profit too."""
    assert label_metrics("Gross profit for the year was $12 billion.") == ("gross_profit",)
    assert label_metrics("Gross margin was 42 percent.") == ("gross_margin",)


def test_pat_margin_maps_to_net_profit_margin():
    text = "PAT margin improved to 15 percent this year."

    assert label_metrics(text) == ("net_profit_margin",)


# --- tier 3: model fallback, only reached when the rules found nothing ---


def test_llm_fallback_accepts_a_metric_from_the_allowed_lexicon():
    client = _SequencedClient([{"reasoning": "...", "metrics": ["net_income"]}])

    result = label_metrics_llm_fallback("some ambiguous sentence", client, lexicon=("net_income", "total_revenue"))

    assert result == ("net_income",)


def test_llm_fallback_drops_a_metric_not_in_the_lexicon():
    """The model never gets to invent a label -- code enforces the closed list."""
    client = _SequencedClient([{"reasoning": "...", "metrics": ["net_income", "made_up_metric"]}])

    result = label_metrics_llm_fallback("some sentence", client, lexicon=("net_income",))

    assert result == ("net_income",)


def test_llm_fallback_empty_list_is_a_correct_unlabelled_answer():
    client = _SequencedClient([{"reasoning": "...", "metrics": []}])

    result = label_metrics_llm_fallback("an unrelated sentence", client, lexicon=("net_income",))

    assert result == ()


def test_llm_fallback_schema_invalid_response_is_dropped_not_guessed():
    client = _SequencedClient([{"reasoning": "..."}])  # missing required "metrics" field

    result = label_metrics_llm_fallback("some sentence", client, lexicon=("net_income",))

    assert result == ()


def test_llm_fallback_sends_the_lexicon_and_the_sentence():
    client = _SequencedClient([{"reasoning": "...", "metrics": []}])

    label_metrics_llm_fallback("the sentence text", client, lexicon=("net_income", "total_revenue"))

    assert client.calls[0]["sentence"] == "the sentence text"
    assert client.calls[0]["allowed_metrics"] == ["net_income", "total_revenue"]


# --- the dispatcher: rules first, model only for leftovers ---


def test_with_fallback_uses_rules_and_never_calls_the_model_when_rules_succeed():
    client = _SequencedClient([])  # would raise IndexError if called

    result = label_metrics_with_fallback("Acme Corp had net income of $5 million.", llm_client=client)

    assert result == ("net_income",)
    assert client.calls == []


def test_with_fallback_calls_the_model_only_when_rules_found_nothing():
    client = _SequencedClient([{"reasoning": "...", "metrics": ["net_income"]}])

    result = label_metrics_with_fallback("some ambiguous sentence rules can't parse", llm_client=client)

    assert result == ("net_income",)
    assert len(client.calls) == 1


def test_with_fallback_without_a_client_stays_unlabelled_exactly_like_before():
    result = label_metrics_with_fallback("some ambiguous sentence rules can't parse")  # llm_client=None

    assert result == ()


def test_with_fallback_lexicon_passed_to_the_model_matches_the_alias_dictionary_keys():
    client = _SequencedClient([{"reasoning": "...", "metrics": []}])

    label_metrics_with_fallback("some ambiguous sentence", llm_client=client)

    assert set(client.calls[0]["allowed_metrics"]) == set(DEFAULT_METRIC_ALIASES)
