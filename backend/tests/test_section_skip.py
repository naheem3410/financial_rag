"""Item 1A "Risk Factors" section-skip tests.
TestRealWorld3M uses verbatim excerpts from the real 3M_2018_10K PDF, not synthetic what-ifs.
"""
from __future__ import annotations

from offline.parsers.layout_parser import ParsedPage
from offline.parsers.section_skip import find_risk_factors_section, skip_risk_factors_section


def _page(page_number: int, text: str) -> ParsedPage:
    return ParsedPage(page_number=page_number, text=text, tables=[])


class TestRealWorld3M:
    """Verbatim excerpts from data/pdfs/3M_2018_10K.pdf, pages 1 and 9-11."""

    TOC_PAGE = (
        "Table of Contents\n3M COMPANY\nFORM 10-K\nFor the Year Ended December 31, 2018\n"
        "Pursuant to Part IV, Item 16, a summary of Form 10-K content follows...\n"
        "Item 1. Business 3\nItem 1A. Risk Factors 10\nItem 1B. Unresolved Staff Comments 12\n"
        "Item 2. Properties 12\nItem 3. Legal Proceedings 15\nItem 4. Mine Safety Disclosures 16"
    )

    # page 9 has both a narrative cross-reference and the real heading, mid-page
    PAGE_9_WITH_CROSS_REFERENCE_AND_HEADING = (
        "Table of Contents\nForward-looking statements are based on certain assumptions and "
        "expectations of future events and trends that are subject to risks and uncertainties. "
        "Actual future results and trends may differ materially from historical results or those "
        "reflected in any such forward-looking statements depending on a variety of factors. "
        "Important information as to these factors can be found accompanying such forward-looking "
        "statements in Item 1 above and in Item 7 below, as well as within Item 1A of this document. "
        "Except to the extent required by applicable law, we do not undertake to update any "
        "forward-looking statement. A discussion of these factors is incorporated by reference from "
        "Part I, Item 1A, “Risk Factors,” of this document, and should be considered an integral "
        "part of Part I, Item 1, “Business,” and of this Form 10-K, 10-Q and 8-K filed with the SEC "
        "from time to time.\n"
        "Item 1A. Risk Factors\n"
        "Provided below is a cautionary discussion of what we believe are the most important risk "
        "factors applicable to the Company."
    )

    PAGE_10_CONTINUATION_NO_HEADING = (
        "Table of Contents\n* The Company’s growth objectives are largely dependent on the timing "
        "and market acceptance of its new product offerings, including its ability to continually "
        "renew its pipeline of new products and to bring those products to market."
    )

    # page 11: tail of risk-factor bullets, then Item 1B / Item 2 headings
    PAGE_11_WITH_END_MARKERS = (
        "Table of Contents\nassets, and relevant legislative or regulatory changes relating to "
        "defined benefit plan funding may increase the Company's funding obligations and adversely "
        "impact its results of operations and cash flows.\n"
        "Item 1B. Unresolved Staff Comment s.\nNone.\n"
        "Item 2. Propertie s.\nIn the U.S., 3M’s general offices are located in St. Paul, Minnesota."
    )

    def _pages(self):
        return [
            _page(1, self.TOC_PAGE),
            _page(9, self.PAGE_9_WITH_CROSS_REFERENCE_AND_HEADING),
            _page(10, self.PAGE_10_CONTINUATION_NO_HEADING),
            _page(11, self.PAGE_11_WITH_END_MARKERS),
            _page(12, "Table of Contents\nPART II\nItem 5. Market for Registrant's Common Equity"),
        ]

    def test_toc_entry_is_not_mistaken_for_the_real_heading(self):
        """TOC page alone must yield no section -- it's a listing, not the real start."""
        section = find_risk_factors_section([_page(1, self.TOC_PAGE)])
        assert section is None

    def test_narrative_cross_reference_is_not_mistaken_for_the_real_heading(self):
        """Page 9 has an earlier cross-reference before the real heading.
        Must match via the real heading, not the cross-reference the comma-lookback guards against."""
        section = find_risk_factors_section(self._pages())
        assert section is not None
        assert section.start_page_number == 9

    def test_section_end_is_the_page_bearing_item_1b(self):
        section = find_risk_factors_section(self._pages())
        assert section is not None
        assert section.end_page_number == 11  # exclusive: page 11 is kept

    def test_skip_removes_pages_9_and_10_only(self):
        kept, skipped = skip_risk_factors_section(self._pages())
        kept_numbers = [p.page_number for p in kept]
        assert kept_numbers == [1, 11, 12]
        assert set(skipped.keys()) == {9, 10}
        assert all(s.reason == "risk_factors_item_1a" for s in skipped.values())

    def test_page_11_is_kept_despite_trailing_risk_factors_text(self):
        """Page 11 packs Item 1B/2/3/4 alongside a residual risk-factors
        sentence -- page-level granularity keeps both, that trade-off is intentional."""
        kept, _ = skip_risk_factors_section(self._pages())
        page_11 = next(p for p in kept if p.page_number == 11)
        assert "Item 1B" in page_11.text
        assert "Item 2" in page_11.text


def test_no_heading_found_yields_no_skip():
    pages = [_page(0, "Cover page text."), _page(1, "Ordinary business narrative, no risk factors.")]
    kept, skipped = skip_risk_factors_section(pages)
    assert kept == pages
    assert skipped == {}


def test_heading_with_no_end_marker_skips_through_the_last_page():
    pages = [
        _page(0, "Item 1A. Risk Factors\nSome risk narrative here."),
        _page(1, "More risk narrative, document ends without a next item heading."),
    ]
    section = find_risk_factors_section(pages)
    assert section is not None
    assert section.start_page_number == 0
    assert section.end_page_number == 2  # exclusive, one past the last real page

    kept, skipped = skip_risk_factors_section(pages)
    assert kept == []
    assert set(skipped.keys()) == {0, 1}


def test_item_2_alone_can_terminate_the_section_when_1b_is_absent():
    """Some filers skip Item 1B and go straight from Risk Factors to Item 2.
    End-marker search must still catch it."""
    pages = [
        _page(0, "Item 1A. Risk Factors\nRisk narrative."),
        _page(1, "Item 2. Properties\nProperty narrative."),
    ]
    section = find_risk_factors_section(pages)
    assert section is not None
    assert section.end_page_number == 1


def test_toc_style_end_marker_is_not_mistaken_for_the_real_end():
    """A TOC page between the real heading and end marker must not be
    mistaken for the end just because it lists 'Item 1B ... 12'."""
    pages = [
        _page(0, "Item 1A. Risk Factors\nRisk narrative begins here."),
        _page(1, "Item 1A. Risk Factors 10\nItem 1B. Unresolved Staff Comments 12\nItem 2. Properties 12"),
        _page(2, "More risk narrative, still no real end marker on this page."),
        _page(3, "Item 1B. Unresolved Staff Comments\nNone."),
    ]
    section = find_risk_factors_section(pages)
    assert section is not None
    assert section.end_page_number == 3
