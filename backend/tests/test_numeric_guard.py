"""Period-verbatim guard tests. See numeric_guard.py for the
fbc_metric_pri_0061 finding that motivated this guard."""
from __future__ import annotations

from online.curation.numeric_guard import validate_period_verbatim


def test_source_reports_a_different_year_than_the_fff_asserts_rejects():
    """The fbc_metric_pri_0061 mechanism: source states Dec 31 2022, an FFF
    claiming FY2023 must reject same as a fabricated number."""
    source = "3M Company had accounts payable of $3,183 million as of December 31, 2022."
    assert validate_period_verbatim("FY2023", source) is False


def test_correctly_labeled_fff_passes():
    source = "3M Company had accounts payable of $3,183 million as of December 31, 2022."
    assert validate_period_verbatim("FY2022", source) is True
    assert validate_period_verbatim("year ended December 31, 2022", source) is True


def test_matching_quarter_passes_mismatched_quarter_rejects():
    source = "3M Company had total current liabilities of $10,936 million as of June 30, 2023."
    # "as of <date>" parses to (year, None) -- annual-shaped -- so a Q2
    # label genuinely disagrees (None != 2) and must be rejected.
    assert validate_period_verbatim("Q2 FY2023", source) is False
    assert validate_period_verbatim("FY2023", source) is True


def test_unparseable_period_label_does_not_reject():
    """Never guess a violation it cannot confirm -- matches
    validate_numeric_verbatim's "nothing to verify -> pass" contract."""
    source = "3M Company had accounts payable of $3,183 million as of December 31, 2022."
    assert validate_period_verbatim("the most recent reporting period", source) is True


def test_unparseable_source_does_not_reject():
    source = "Revenue grew across all segments due to strong demand."
    assert validate_period_verbatim("FY2023", source) is True


def test_genuinely_different_fiscal_years_always_reject_however_phrased():
    source = "3M Company's revenue for the year ended December 31, 2021 was $35.4 billion."
    assert validate_period_verbatim("FY2023", source) is False
    assert validate_period_verbatim("fiscal year 2023", source) is False


def test_guard_is_deterministic_zero_llm_calls(llm_call_counter):
    source = "3M Company had accounts payable of $3,183 million as of December 31, 2022."
    validate_period_verbatim("FY2023", source)
    validate_period_verbatim("FY2022", source)
    assert llm_call_counter.count == 0
