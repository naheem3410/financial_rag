"""Numeric/unit/currency/period extractor tests."""
from __future__ import annotations

from offline.extraction.numeric_extractor import extract, extract_tier2, extract_tier3


class _FakeLLMClient:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._response


def test_tier2_resolves_explicit_quarter_and_fiscal_year():
    record = extract_tier2("Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")

    assert record is not None
    assert record.value == 18.2
    assert record.unit == "percent"
    assert record.period.fiscal_year == 2024
    assert record.period.quarter == 3


def test_tier2_resolves_fiscal_year_only():
    record = extract_tier2("Acme Corp net sales for fiscal year 2018 were $32.2 billion.")

    assert record is not None
    assert record.value == 32.2
    assert record.scale == "billions"
    assert record.period.fiscal_year == 2018
    assert record.period.quarter is None


def test_tier2_resolves_year_ended_phrasing():
    """Regression, 2026-07-25: the real Proposition Segmenter restates
    statement headers as "in the year ended December 31, 2018" — not
    "FY2018" shorthand. Found reviewing the real 5-document build sample,
    where tier2 was silently returning numeric=None for nearly every real
    proposition before this pattern was added."""
    record = extract_tier2("3M Company had net sales of $32,765 million in the year ended December 31, 2018.")

    assert record is not None
    assert record.value == 32765.0
    assert record.scale == "millions"
    assert record.period.fiscal_year == 2018
    assert record.period.quarter is None


def test_tier2_resolves_quarter_ended_phrasing_via_calendar_month():
    record = extract_tier2("Acme Corp had segment revenue of $8.5 billion for the quarter ended June 30, 2023.")

    assert record is not None
    assert record.period.fiscal_year == 2023
    assert record.period.quarter == 2  # June -> calendar Q2


def test_tier2_resolves_negative_accounting_value_with_year_ended_phrasing():
    record = extract_tier2(
        "3M Company had a cumulative translation adjustment of $(467) million net of tax "
        "in the year ended December 31, 2018."
    )

    assert record is not None
    assert record.value == -467.0
    assert record.period.fiscal_year == 2018


def test_tier2_resolves_balance_sheet_at_date_phrasing():
    """Regression, 2026-07-27: a real C3 (NPCE) run found the Metadata
    Filter returning zero candidates for every balance-sheet metric
    (accounts payable/receivable, inventories, total current assets/
    liabilities) -- tier2 only recognized "year/quarter ended" phrasing,
    which flow/income-statement propositions use; a balance sheet reports a
    point-in-time snapshot ("at <date>"), never a period "ended". This gap
    was invisible until the Metadata Filter was wired into a real query
    path for the first time (C1/C2 never query by fiscal_year at all)."""
    record = extract_tier2("3M Company had accounts payable of 2,266 million at December 31, 2018.")

    assert record is not None
    assert record.value == 2266.0
    assert record.scale == "millions"
    assert record.period.fiscal_year == 2018
    assert record.period.quarter == 4  # December -> calendar Q4


def test_tier2_resolves_balance_sheet_as_of_phrasing():
    record = extract_tier2("3M Company had accounts receivable of 4,947 million as of June 30, 2023.")

    assert record is not None
    assert record.period.fiscal_year == 2023
    assert record.period.quarter == 2  # June -> calendar Q2


def test_tier2_returns_none_when_period_is_ambiguous():
    assert extract_tier2("Acme Corp operating margin for the prior period was 18.2 percent.") is None


def test_tier2_never_calls_the_llm(llm_call_counter):
    extract_tier2("Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")

    assert llm_call_counter.count == 0


def test_tier3_resolves_ambiguous_period_via_llm_classification():
    client = _FakeLLMClient(
        {
            "reasoning": "The prior period follows the fiscal year ending in this filing.",
            "fiscal_year": 2023,
            "quarter": 3,
            "basis": "reported",
            "scale": None,
            "currency": "USD",
            "resolvable": True,
        }
    )

    record = extract_tier3(
        "Acme Corp operating margin for the prior period was 18.2 percent.",
        ambiguous_qualifier="the prior period",
        document_context={"company": "Acme Corp", "fiscal_year": 2024},
        llm_client=client,
    )

    assert record is not None
    assert record.value == 18.2  # copied verbatim from text by code, not the model
    assert record.period.fiscal_year == 2023
    assert record.period.quarter == 3


def test_tier3_drops_when_llm_returns_unresolvable():
    client = _FakeLLMClient({"reasoning": "Context does not determine the period.", "resolvable": False})

    record = extract_tier3(
        "Acme Corp operating margin for the prior period was 18.2 percent.",
        ambiguous_qualifier="the prior period",
        document_context={},
        llm_client=client,
    )

    assert record is None


def test_tier3_drops_on_schema_invalid_response_never_repairs():
    # digit leaked into reasoning -> schema-invalid per llm/schemas.py
    client = _FakeLLMClient(
        {"reasoning": "This is fiscal year 2023.", "resolvable": True, "fiscal_year": 2023}
    )

    record = extract_tier3(
        "Acme Corp operating margin for the prior period was 18.2 percent.",
        ambiguous_qualifier="the prior period",
        document_context={},
        llm_client=client,
    )

    assert record is None


def test_dispatcher_prefers_tier2_and_never_calls_llm_when_it_resolves():
    client = _FakeLLMClient({"reasoning": "unused", "resolvable": True})

    result = extract(
        "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.",
        document_context={},
        llm_client=client,
    )

    assert result.numeric is not None
    assert result.extraction_tier == "rules"
    assert client.calls == []


def test_dispatcher_falls_back_to_tier3_when_period_is_ambiguous():
    client = _FakeLLMClient(
        {
            "reasoning": "Resolved from filing context.",
            "fiscal_year": 2023,
            "quarter": 3,
            "basis": "reported",
            "scale": None,
            "currency": "USD",
            "resolvable": True,
        }
    )

    result = extract(
        "Acme Corp operating margin for the prior period was 18.2 percent.",
        document_context={},
        llm_client=client,
        ambiguous_qualifier="the prior period",
    )

    assert result.numeric is not None
    assert result.extraction_tier == "llm_qualifier"
    assert len(client.calls) == 1


def test_dispatcher_returns_none_tier_when_unresolvable_and_no_qualifier_hint():
    client = _FakeLLMClient({"reasoning": "unused", "resolvable": True})

    result = extract(
        "Acme Corp operating margin for the prior period was 18.2 percent.",
        document_context={},
        llm_client=client,
        ambiguous_qualifier=None,
    )

    assert result.numeric is None
    assert result.extraction_tier is None
    assert client.calls == []
