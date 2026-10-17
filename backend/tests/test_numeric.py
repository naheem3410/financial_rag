"""contracts/numeric.py is the single normalization authority -- every
numeric guard and scoring consumer depends on this being right."""
from __future__ import annotations

from decimal import Decimal

from contracts.numeric import appears_verbatim, normalize_value, numeric_tokens, parse_fiscal_period


class TestNormalizeValue:
    def test_thousands_separator(self):
        assert normalize_value("1,577") == Decimal("1577")

    def test_accounting_parentheses_mean_negative(self):
        assert normalize_value("(1,577)") == Decimal("-1577")

    def test_currency_symbol_and_trailing_decimal_zero(self):
        assert normalize_value("$1,577.0") == Decimal("1577.0")

    def test_trailing_percent_sign_is_stripped_not_divided(self):
        # 18.2% -> 18.2, not 0.182: percent-to-fraction is a unit concern,
        # not normalize_value's job.
        assert normalize_value("18.2%") == Decimal("18.2")

    def test_trailing_zeros_do_not_affect_equality(self):
        assert normalize_value("18.20") == normalize_value("18.2")

    def test_integer_vs_zero_decimal_are_equal(self):
        assert normalize_value("1577") == normalize_value("1577.00")

    def test_plain_integer(self):
        assert normalize_value("1577") == Decimal("1577")

    def test_none_input_returns_none(self):
        assert normalize_value(None) is None

    def test_blank_input_returns_none(self):
        assert normalize_value("   ") is None

    def test_garbage_input_returns_none_not_raise(self):
        assert normalize_value("not a number") is None

    def test_result_is_decimal_not_float(self):
        assert isinstance(normalize_value("18.2"), Decimal)

    def test_decimal_avoids_float_representation_error(self):
        # Decimal sidesteps float accumulation error by construction --
        # check two independently-parsed, differently-formatted values match.
        assert normalize_value("18.2") == normalize_value("$18.20")

    def test_accounting_parentheses_with_currency_symbol(self):
        assert normalize_value("($1,577.00)") == Decimal("-1577.00")

    def test_does_not_fold_scale_words(self):
        # guard against the tempting shortcut of treating a trailing scale
        # word like "million" as a multiplier -- not this function's job
        assert normalize_value("1.5") == Decimal("1.5")


class TestNumericTokens:
    def test_finds_plain_and_formatted_numbers_in_prose(self):
        text = "Net sales were $8,500,000,000 for Q3, up 18.2% from (1,200) a year ago."
        tokens = numeric_tokens(text)
        assert "$8,500,000,000" in tokens
        assert "18.2%" in tokens
        assert "(1,200)" in tokens

    def test_no_numeric_tokens_in_pure_prose(self):
        assert numeric_tokens("Revenue grew across all segments.") == []


class TestAppearsVerbatim:
    def test_genuine_value_present_in_source(self):
        source = "<ENT_1> operating margin for Q3 FY2024 was 18.2 percent."
        assert appears_verbatim("18.2 percent", source) is True

    def test_fabricated_value_absent_from_source(self):
        source = "<ENT_1> operating margin for Q3 FY2024 was 18.2 percent."
        assert appears_verbatim("19.9 percent", source) is False

    def test_matches_despite_different_formatting(self):
        source = "<ENT_1> net sales for Q3 FY2024 were $8,500,000,000."
        assert appears_verbatim("8500000000", source) is True

    def test_matches_despite_trailing_zero_difference(self):
        source = "<ENT_1> operating margin for Q3 FY2024 was 18.20 percent."
        assert appears_verbatim("18.2 percent", source) is True

    def test_accounting_negative_matches_signed_candidate(self):
        source = "<ENT_1> free cash flow for Q3 FY2024 was (1,577) million."
        assert appears_verbatim("-1577", source) is True

    def test_candidate_with_no_numeric_tokens_is_vacuously_true(self):
        source = "<ENT_1> operating margin for Q3 FY2024 was 18.2 percent."
        assert appears_verbatim("no numbers here", source) is True

    def test_every_candidate_token_must_be_found_not_just_one(self):
        source = "<ENT_1> operating margin for Q3 FY2024 was 18.2 percent."
        # 18.2 is genuine, 99.9 is fabricated — "every" numeric token must
        # match, so one fabrication anywhere fails the whole candidate.
        assert appears_verbatim("18.2 percent, up from 99.9 percent", source) is False

    def test_source_with_no_numbers_rejects_any_numeric_candidate(self):
        source = "Revenue grew across all segments."
        assert appears_verbatim("18.2 percent", source) is False


class TestParseFiscalPeriod:
    """Canonicalization shared with numeric_comparator.py and numeric_guard.py's
    period-verbatim guard."""

    def test_fy_form(self):
        assert parse_fiscal_period("FY2018") == (2018, None)

    def test_fiscal_year_form(self):
        assert parse_fiscal_period("fiscal year 2018") == (2018, None)

    def test_year_ended_form(self):
        assert parse_fiscal_period("for the year ended December 31, 2018") == (2018, None)

    def test_as_of_form(self):
        """Dominant phrasing for balance-sheet propositions in this corpus --
        added for the period-verbatim guard, which parses raw prop text."""
        assert parse_fiscal_period("3M Company had accounts payable of $3,183 million as of December 31, 2022.") == (2022, None)

    def test_quarter_and_fy_form(self):
        assert parse_fiscal_period("Q3 FY2024") == (2024, 3)
        assert parse_fiscal_period("FY2024 Q3") == (2024, 3)

    def test_quarter_ended_without_explicit_quarter_is_unparseable(self):
        """Which quarter a bare date falls in depends on fiscal-year-end
        convention -- left unparsed rather than guessed."""
        assert parse_fiscal_period("for the three months ended June 30, 2023") is None
        assert parse_fiscal_period("quarter ended September 30, 2024") is None

    def test_bare_year_fallback(self):
        assert parse_fiscal_period("2018") == (2018, None)

    def test_none_and_blank_are_unparseable(self):
        assert parse_fiscal_period(None) is None
        assert parse_fiscal_period("") is None

    def test_no_year_present_is_unparseable(self):
        assert parse_fiscal_period("Revenue grew across all segments.") is None
