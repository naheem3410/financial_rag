"""Content-based page filter tests. TestRealWorldFalsePositives uses
verbatim excerpts from the real 3M_2018_10K PDF the pilot misclassified."""
from __future__ import annotations

from offline.parsers.layout_parser import ParsedPage
from offline.parsers.page_filter import classify_page, filter_pages


def _page(page_number: int, text: str) -> ParsedPage:
    return ParsedPage(page_number=page_number, text=text, tables=[])


def test_cover_page_is_skipped():
    text = (
        "UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nWashington, D.C. 20549\n\n"
        "FORM 10-K\n\nCommission File Number: 1-3285\n\n3M COMPANY"
    )
    assert classify_page(_page(0, text)) == "cover"


def test_table_of_contents_is_skipped_when_item_entries_are_dense():
    text = (
        "TABLE OF CONTENTS\n\n"
        "Item 1. Business ... 3\nItem 1A. Risk Factors ... 10\nItem 2. Properties ... 12\n"
        "Item 3. Legal Proceedings ... 15\nItem 4. Mine Safety Disclosures ... 16\n"
        "Item 5. Market for Registrant's Common Equity ... 17"
    )
    assert classify_page(_page(1, text)) == "table_of_contents"


def test_a_single_item_reference_in_narrative_is_not_flagged_as_toc():
    """Density, not the bare phrase, is the signal -- one or two incidental
    "Item N" mentions in prose must not trigger TOC classification."""
    text = (
        "As discussed in Item 7 of this report, management's assessment of "
        "liquidity remains consistent with the prior fiscal year."
    )
    assert classify_page(_page(30, text)) is None


def test_signature_page_is_skipped():
    text = (
        "SIGNATURES\n\n"
        "Pursuant to the requirements of Section 13 or 15(d) of the Securities Exchange Act of 1934, "
        "the registrant has duly caused this report to be signed.\n\n"
        "/s/ Jane Doe\nChief Executive Officer\n\n/s/ John Smith\nChief Financial Officer"
    )
    assert classify_page(_page(50, text)) == "signatures"


def test_exhibit_index_is_skipped():
    text = "EXHIBIT INDEX\n\nExhibit No. Description\n3.1 Articles of Incorporation\n10.1 Credit Agreement"
    assert classify_page(_page(48, text)) == "exhibit_index"


def test_certification_page_is_skipped():
    text = (
        "CERTIFICATIONS\n\nI, Jane Doe, certify that:\n1. I have reviewed this annual report on Form 10-K...\n"
        "pursuant to Section 302 of the Sarbanes-Oxley Act of 2002"
    )
    assert classify_page(_page(52, text)) == "certification"


def test_ordinary_financial_content_page_is_not_skipped():
    text = (
        "Management's Discussion and Analysis\n\n"
        "3M Company reported net sales of $32.2 billion for fiscal year 2018. "
        "Operating margin was 18.2 percent, driven by organic growth across all business segments."
    )
    assert classify_page(_page(33, text)) is None


def test_table_page_with_financial_data_is_not_skipped():
    text = "Consolidated Statement of Operations\n\nLine Item FY2023 FY2024\nOperating margin 16.5% 18.2%"
    assert classify_page(_page(34, text)) is None


def test_filter_pages_splits_kept_and_skipped_with_reasons():
    pages = [
        _page(0, "UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nCommission File Number: 1-3285"),
        _page(1, "3M Company reported net sales of $32.2 billion for fiscal year 2018."),
        _page(
            2,
            "TABLE OF CONTENTS\n\nItem 1. Business ... 3\nItem 1A. Risk Factors ... 10\n"
            "Item 2. Properties ... 12\nItem 3. Legal Proceedings ... 15\nItem 4. Mine Safety ... 16",
        ),
    ]

    kept, skipped = filter_pages(pages)

    assert [p.page_number for p in kept] == [1]
    assert skipped == {0: "cover", 2: "table_of_contents"}


def test_filter_pages_with_nothing_to_skip_keeps_everything():
    pages = [_page(0, "3M Company reported net sales of $32.2 billion for fiscal year 2018.")]

    kept, skipped = filter_pages(pages)

    assert len(kept) == 1
    assert skipped == {}


class TestRealWorldFalsePositives:
    """Regression tests for the 2026-07-25 pilot finding: verbatim excerpts
    from the real 3M_2018_10K PDF (page numbers refer to that document)."""

    def test_running_table_of_contents_header_does_not_flag_ordinary_content(self):
        """3M prints "Table of Contents" as a running header on every page --
        page 40 was misclassified as the TOC listing before the density fix."""
        text = (
            "Table of Contents\n"
            "Asset Impairments:\n"
            "As of December 31, 2018, net property, plant and equipment totaled $8.7 billion and net "
            "identifiable intangible assets totaled $2.7 billion. Management\n"
            "makes estimates and assumptions in preparing the consolidated financial statements for "
            "which actual results will differ."
        )
        assert classify_page(_page(40, text)) is None

    def test_class_certification_in_litigation_narrative_is_not_flagged(self):
        """Real page 120: "certification" in the "class certification" legal
        sense, not an SEC officer certification page."""
        text = (
            "Table of Contents\n"
            "In June 2016, the Company was served with a putative class action filed in the Ontario "
            "Superior Court of Justice for all Canadian residents who underwent\n"
            "various joint arthroplasty, cardiovascular, and other surgeries and later developed "
            "surgical site infections due to the use of the Bair Hugger patient\n"
            "warming system. The representative plaintiff seeks relief (including punitive damages) "
            "and class certification."
        )
        assert classify_page(_page(120, text)) is None

    def test_real_toc_page_with_unpunctuated_item_numbers_is_detected(self):
        """Real page 1: items formatted "ITEM 1" with no trailing period --
        the density check must not require one."""
        text = (
            "Table of Contents\n3M COMPANY\nFORM 10-K\nFor the Year Ended December 31, 2018\n"
            "Beginning\nPage\nPART I\n"
            "ITEM 1 Business 4\nITEM 1A Risk Factors 10\nITEM 1B Unresolved Staff Comments 12\n"
            "ITEM 2 Properties 12\nITEM 3 Legal Proceedings 12\nITEM 4 Mine Safety Disclosures 12\n"
            "PART II\nITEM 5 Market for Registrant's Common Equity 13\nITEM 6 Selected Financial Data 14"
        )
        assert classify_page(_page(1, text)) == "table_of_contents"
