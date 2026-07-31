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
    """A right number in the wrong period is not a match -- exactly the
    silent temporal-collision error this comparator exists to catch."""
    assert numeric_match(_claim(period="Q3 FY2024"), _verification(period="Q2 FY2024")) is False


def test_conflict_when_scale_differs():
    """A right number at the wrong scale (millions vs billions) is not a
    match -- a silent 1000x error, not a rounding difference."""
    assert numeric_match(_claim(scale="millions"), _verification(scale="billions")) is False


def test_unparseable_claim_value_never_matches():
    assert numeric_match(_claim(value="not a number"), _verification(value="18.2")) is False


def test_both_unparseable_does_not_match_by_coincidence():
    """Two garbage values must not accidentally 'match' just because both
    fail to parse to the same None."""
    assert numeric_match(_claim(value="garbage"), _verification(value="also garbage")) is False


# --- Period/unit normalization (added 2026-07-26, real C2 smoke-test finding
# on fbc_metric_pri_0024 -- see module docstring) ---


def test_same_fiscal_year_different_phrasing_still_matches():
    """The exact false-positive found live: claim extraction says 'FY2018'
    (from the draft), independent verification says 'year ended December
    31, 2018' (echoing the evidence's own wording) -- same fiscal year, must
    not read as a conflict."""
    assert numeric_match(
        _claim(period="FY2018"), _verification(period="year ended December 31, 2018")
    ) is True


def test_fiscal_year_and_year_ended_prefixed_text_still_matches():
    assert numeric_match(
        _claim(period="FY2018"), _verification(period="for the year ended December 31, 2018")
    ) is True


def test_different_fiscal_years_still_conflict_after_normalization():
    """The 0061 temporal-collision mechanism must survive normalization
    untouched: two genuinely different fiscal years must never be
    collapsed together, however they're phrased."""
    assert numeric_match(_claim(period="FY2023"), _verification(period="FY2022")) is False
    assert numeric_match(
        _claim(period="FY2023"), _verification(period="year ended December 31, 2022")
    ) is False


def test_quarter_does_not_match_annual_for_the_same_year():
    """A quarterly figure and an annual figure for the same calendar year
    are different periods, not a phrasing variant of each other."""
    assert numeric_match(_claim(period="FY2024"), _verification(period="Q3 FY2024")) is False


def test_quarter_ended_bare_date_is_unparsed_and_falls_back_to_string_compare():
    """A 'quarter ended <date>' phrasing with no explicit Q-number is
    deliberately left unparsed (inferring the quarter from a date requires
    knowing a company's fiscal-year-end convention, which this comparator
    cannot know safely) -- falls back to the pre-existing raw string
    comparison rather than guessing."""
    assert numeric_match(
        _claim(period="Q3 FY2024"), _verification(period="quarter ended September 30, 2024")
    ) is False  # falls back to string compare -- different strings, no match either way


def test_unit_scale_field_confusion_is_not_a_conflict():
    """The other real false-positive found live: independent verification
    put a scale word ('millions') in the unit field and never restated the
    real unit -- both sides still genuinely agree the scale is millions, so
    this must not read as a unit conflict just because one side never
    stated a real unit."""
    assert numeric_match(
        _claim(unit="USD", scale="millions"), _verification(unit="millions", scale="millions")
    ) is True


def test_genuine_unit_conflict_still_caught_after_normalization():
    """Two *real*, differently-stated units must still conflict -- the
    field-confusion fix only excuses a side that states no real unit at
    all, it must not excuse an actual disagreement."""
    assert numeric_match(_claim(unit="USD", scale="millions"), _verification(unit="EUR", scale="millions")) is False


def test_value_with_embedded_scale_word_still_matches():
    """A second live finding on the same real question: independent
    verification sometimes embeds the scale word directly into
    answer_value ("$1,848 million" instead of "1,848") -- normalize_value
    doesn't fold scale words and doesn't strip them either, so the value
    would silently fail to parse without stripping first (the same failure
    already fixed once in evaluation/run_c1_gate.py's scoring)."""
    assert numeric_match(
        _claim(value="1848", unit="USD", scale="millions"),
        _verification(value="$1,848 million", unit="USD", scale="millions"),
    ) is True


def test_currency_synonyms_do_not_conflict():
    """The same real question showed the two calls rendering the identical
    currency as "$" vs "dollars" -- must not read as a unit conflict."""
    assert numeric_match(_claim(unit="$", scale="millions"), _verification(unit="dollars", scale="millions")) is True


def test_genuine_currency_conflict_still_caught_after_synonym_canonicalization():
    assert numeric_match(_claim(unit="$", scale="millions"), _verification(unit="EUR", scale="millions")) is False


def test_compound_scale_and_currency_phrase_still_matches():
    """A third live finding, on a fuller k=3 C2 run: independent
    verification's answer_unit is sometimes a single compound phrase
    ("millions of dollars") rather than a clean unit -- neither a
    recognized scale word nor a recognized currency synonym on its own, so
    it fell through as "a real, different unit" and conflicted with a
    claim's clean unit="USD"/scale="millions", even though both sides
    agree completely on both the scale and the currency."""
    assert numeric_match(
        _claim(unit="USD", scale="millions"),
        _verification(unit="millions of dollars", scale="millions"),
    ) is True


def test_compound_scale_and_currency_phrase_still_catches_a_real_scale_conflict():
    """The compound-phrase fix must not blind the scale comparison -- a
    genuinely different scale hidden inside a compound phrase still
    conflicts."""
    assert numeric_match(
        _claim(unit="USD", scale="millions"),
        _verification(unit="billions of dollars", scale="billions"),
    ) is False


def test_compound_scale_and_currency_phrase_on_the_claim_side_too():
    assert numeric_match(
        _claim(unit="millions of dollars", scale="millions"),
        _verification(unit="USD", scale="millions"),
    ) is True
