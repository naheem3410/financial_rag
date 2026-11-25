"""Filing period-end date extraction tests.

TestRealWorldCoverPages uses verbatim excerpts from the real corpus's cover pages,
including Adobe's Dec 2 (not Dec 31) fiscal year end, the reason we read the stated date.
"""
from __future__ import annotations

from offline.parsers.filing_date import extract_filing_period_end
from offline.parsers.layout_parser import ParsedPage


def _page(page_number: int, text: str) -> ParsedPage:
    return ParsedPage(page_number=page_number, text=text, tables=[])


class TestRealWorldCoverPages:
    def test_3m_10k_annual_period(self):
        text = (
            "UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nWashington, D.C. 20549\nFORM 10-K\n"
            "ANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE\nSECURITIES EXCHANGE ACT OF 1934\n"
            "For the fiscal year ended December 31, 2018\nCommission file number 1-3285\n3M COMPANY"
        )
        assert extract_filing_period_end([_page(0, text)]) == "2018-12-31"

    def test_3m_10q_quarterly_period(self):
        text = (
            "UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nWASHINGTON, D.C. 20549\nFORM 10-Q\n"
            "QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES EXCHANGE ACT OF 1934\n"
            "For the quarterly period ended June 30, 2023\nor"
        )
        assert extract_filing_period_end([_page(0, text)]) == "2023-06-30"

    def test_activision_10k_capitalized_fiscal_year(self):
        """"Fiscal Year Ended" is capitalized differently than 3M's cover page - match must be case-insensitive."""
        text = (
            "FORM 10-K\nANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES EXCHANGE ACT OF 1934\n"
            "For the Fiscal Year Ended December 31, 2019\nOR"
        )
        assert extract_filing_period_end([_page(0, text)]) == "2019-12-31"

    def test_adobe_10k_non_calendar_fiscal_year_end(self):
        """The real-world case that motivated reading the stated date instead of assuming Dec 31:
        Adobe's fiscal year actually ends December 2."""
        text = (
            "FORM 10-K\n(Mark One)\nANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES "
            "EXCHANGE ACT OF 1934\nFor the fiscal year ended December 2, 2016\nor"
        )
        assert extract_filing_period_end([_page(0, text)]) == "2016-12-02"


def test_returns_none_when_no_page_states_the_boilerplate_phrase():
    pages = [_page(0, "Some unrelated cover text with no period statement.")]
    assert extract_filing_period_end(pages) is None


def test_searches_beyond_the_first_page_if_needed():
    pages = [
        _page(0, "Table of Contents"),
        _page(1, "For the fiscal year ended December 31, 2022\n"),
    ]
    assert extract_filing_period_end(pages) == "2022-12-31"


def test_transition_period_phrasing_is_not_mistaken_for_the_real_period():
    """Cover pages also print an unused "transition period" line with blank underscores.
    Must not match that and return garbage - the real period line should win."""
    pages = [
        _page(
            0,
            "For the fiscal year ended December 31, 2018\n"
            "or\nTRANSITION REPORT PURSUANT TO SECTION 13\n"
            "For the transition period from __________ to __________",
        )
    ]
    assert extract_filing_period_end(pages) == "2018-12-31"
