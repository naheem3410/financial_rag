"""CoVe deterministic numeric comparison tests."""
from __future__ import annotations

from online.verification.numeric_comparator import numeric_match


def _claim(value="18.2", unit="percent", period="Q3 FY2024", scale="unit"):
    return {"asserted_value": value, "asserted_unit": unit, "period_label": period, "scale": scale}


def _verification(value="18.2", unit="percent", period="Q3 FY2024", scale="unit"):
    return {"answer_value": value, "answer_unit": unit, "period_label": period, "scale": scale}


def test_matches_when_everything_agrees(llm_call_counter):
    assert numeric_match(_claim(), _verification()) is True
    assert llm_call_counter.count == 0


def test_value_differs_by_display_formatting_still_matches():
    """18.2 vs 18.20 are numerically identical after normalize_value --
    a formatting difference is not a conflict."""
    assert numeric_match(_claim(value="18.2"), _verification(value="18.20")) is True


def test_conflict_when_value_genuinely_differs():
    assert numeric_match(_claim(value="18.2"), _verification(value="19.9")) is False


def test_conflict_when_unit_differs():
    assert numeric_match(_claim(unit="percent"), _verification(unit="ratio")) is False


def test_conflict_when_period_differs():
    """Right number, wrong period is not a match -- the temporal-collision
    error this comparator exists to catch."""
    assert numeric_match(_claim(period="Q3 FY2024"), _verification(period="Q2 FY2024")) is False


def test_conflict_when_scale_differs():
    """Right number, wrong scale (millions vs billions) is a silent 1000x
    error, not a rounding difference."""
    assert numeric_match(_claim(scale="millions"), _verification(scale="billions")) is False


def test_unparseable_claim_value_never_matches():
    assert numeric_match(_claim(value="not a number"), _verification(value="18.2")) is False


def test_both_unparseable_does_not_match_by_coincidence():
    """Two garbage values shouldn't 'match' just because both parse to None."""
    assert numeric_match(_claim(value="garbage"), _verification(value="also garbage")) is False


# Period/unit normalization -- added 2026-07-26 from a real C2 smoke-test
# false positive on fbc_metric_pri_0024.


def test_same_fiscal_year_different_phrasing_still_matches():
    """Claim says 'FY2018', verification echoes evidence wording as 'year
    ended December 31, 2018' -- same year, not a conflict."""
    assert numeric_match(
        _claim(period="FY2018"), _verification(period="year ended December 31, 2018")
    ) is True


def test_fiscal_year_and_year_ended_prefixed_text_still_matches():
    assert numeric_match(
        _claim(period="FY2018"), _verification(period="for the year ended December 31, 2018")
    ) is True


def test_different_fiscal_years_still_conflict_after_normalization():
    """Normalization must never collapse two genuinely different fiscal
    years together, however they're phrased."""
    assert numeric_match(_claim(period="FY2023"), _verification(period="FY2022")) is False
    assert numeric_match(
        _claim(period="FY2023"), _verification(period="year ended December 31, 2022")
    ) is False


def test_quarter_does_not_match_annual_for_the_same_year():
    """Quarterly and annual figures for the same year are different
    periods, not phrasing variants."""
    assert numeric_match(_claim(period="FY2024"), _verification(period="Q3 FY2024")) is False


def test_quarter_ended_bare_date_is_unparsed_and_falls_back_to_string_compare():
    """'quarter ended <date>' with no Q-number is left unparsed since the
    fiscal-year-end convention is unknown -- falls back to string compare."""
    assert numeric_match(
        _claim(period="Q3 FY2024"), _verification(period="quarter ended September 30, 2024")
    ) is False


def test_unit_scale_field_confusion_is_not_a_conflict():
    """Verification put a scale word ('millions') in the unit field instead
    of a real unit -- both sides still agree, not a unit conflict."""
    assert numeric_match(
        _claim(unit="USD", scale="millions"), _verification(unit="millions", scale="millions")
    ) is True


def test_genuine_unit_conflict_still_caught_after_normalization():
    """Two real, differently-stated units must still conflict -- the
    field-confusion fix only excuses a side with no real unit stated."""
    assert numeric_match(_claim(unit="USD", scale="millions"), _verification(unit="EUR", scale="millions")) is False


def test_value_with_embedded_scale_word_still_matches():
    """Verification sometimes embeds the scale word in answer_value
    ("$1,848 million") -- must be stripped before normalize_value parses it."""
    assert numeric_match(
        _claim(value="1848", unit="USD", scale="millions"),
        _verification(value="$1,848 million", unit="USD", scale="millions"),
    ) is True


def test_currency_synonyms_do_not_conflict():
    """The two calls rendered identical currency as "$" vs "dollars" --
    must not read as a unit conflict."""
    assert numeric_match(_claim(unit="$", scale="millions"), _verification(unit="dollars", scale="millions")) is True


def test_genuine_currency_conflict_still_caught_after_synonym_canonicalization():
    assert numeric_match(_claim(unit="$", scale="millions"), _verification(unit="EUR", scale="millions")) is False


def test_compound_scale_and_currency_phrase_still_matches():
    """answer_unit sometimes arrives as a compound phrase ("millions of
    dollars") -- must still match a clean unit="USD"/scale="millions" claim."""
    assert numeric_match(
        _claim(unit="USD", scale="millions"),
        _verification(unit="millions of dollars", scale="millions"),
    ) is True


def test_compound_scale_and_currency_phrase_still_catches_a_real_scale_conflict():
    """The compound-phrase fix must not blind the scale comparison -- a
    different scale hidden inside a compound phrase still conflicts."""
    assert numeric_match(
        _claim(unit="USD", scale="millions"),
        _verification(unit="billions of dollars", scale="billions"),
    ) is False


def test_compound_scale_and_currency_phrase_on_the_claim_side_too():
    assert numeric_match(
        _claim(unit="millions of dollars", scale="millions"),
        _verification(unit="USD", scale="millions"),
    ) is True
