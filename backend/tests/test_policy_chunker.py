"""offline/documents/policy_chunker.py: rule-block chunking for non-finance
documents. Real synthetic policy text, no live PDFs yet (those are still
the owner's to source)."""
from __future__ import annotations

from offline.documents.policy_chunker import chunk_document_into_rule_blocks, chunk_page_into_rule_blocks
from offline.parsers.layout_parser import ParsedDocument, ParsedPage


def test_a_numbered_clause_becomes_one_block():
    text = (
        "4.2.1 Account Eligibility for Minors\n"
        "Applicants under the age of 18 years are permitted to open a custodial "
        "savings account provided that a parent or court-appointed legal guardian "
        "is designated as a joint account holder.\n"
    )

    blocks = chunk_page_into_rule_blocks(text, page=0)

    assert len(blocks) == 1
    assert blocks[0].section_number == "4.2.1"
    assert "custodial savings account" in blocks[0].text


def test_the_whole_paragraph_stays_with_its_heading_not_split_by_sentence():
    text = (
        "4.2.1 Account Eligibility for Minors\n"
        "Minors may open an account. However, they must be accompanied by a "
        "guardian if under 16. This does not apply to emancipated minors.\n"
    )

    blocks = chunk_page_into_rule_blocks(text, page=0)

    assert len(blocks) == 1
    assert "emancipated minors" in blocks[0].text
    assert "guardian if under 16" in blocks[0].text


def test_heading_hierarchy_is_preserved_in_section_path():
    text = (
        "4 Accounts\n"
        "4.2 Eligibility\n"
        "4.2.1 Minors\n"
        "Minors may open an account with a guardian.\n"
    )

    blocks = chunk_page_into_rule_blocks(text, page=0)

    assert len(blocks) == 1
    assert blocks[0].section_path == ("Accounts", "Eligibility", "Minors")


def test_two_sibling_sub_clauses_do_not_bleed_into_each_other():
    text = (
        "4.2.1 Minors\n"
        "Minors may open an account with a guardian.\n"
        "4.2.2 Seniors\n"
        "Seniors receive a fee waiver.\n"
    )

    blocks = chunk_page_into_rule_blocks(text, page=0)

    assert len(blocks) == 2
    assert blocks[0].section_path == ("Minors",)
    assert "guardian" in blocks[0].text
    assert blocks[1].section_path == ("Seniors",)
    assert "fee waiver" in blocks[1].text
    assert "guardian" not in blocks[1].text


def test_returning_to_a_shallower_heading_pops_the_deeper_ones():
    text = (
        "4 Accounts\n"
        "4.1 Opening\n"
        "4.1.1 Documents Required\n"
        "Bring a valid ID.\n"
        "4.2 Closing\n"
        "Submit a written request.\n"
    )

    blocks = chunk_page_into_rule_blocks(text, page=0)

    assert len(blocks) == 2
    assert blocks[0].section_path == ("Accounts", "Opening", "Documents Required")
    assert blocks[1].section_path == ("Accounts", "Closing")  # not ("Accounts", "Opening", "Closing")


def test_named_section_style_heading_is_recognized():
    text = "Section 4.2 - Eligibility\nApplicants must be 18 or older.\n"

    blocks = chunk_page_into_rule_blocks(text, page=0)

    assert len(blocks) == 1
    assert blocks[0].section_number == "4.2"
    assert blocks[0].section_path == ("Eligibility",)


def test_an_unnumbered_plain_heading_is_still_recognized():
    text = "Eligibility\nApplicants must be 18 or older.\n"

    blocks = chunk_page_into_rule_blocks(text, page=0)

    assert len(blocks) == 1
    assert blocks[0].section_number is None
    assert blocks[0].section_path == ("Eligibility",)


def test_text_before_the_first_heading_is_dropped_it_has_no_section_to_cite():
    text = "This document was last reviewed in January.\n4.1 Eligibility\nMust be 18.\n"

    blocks = chunk_page_into_rule_blocks(text, page=0)

    assert len(blocks) == 1
    assert "last reviewed" not in blocks[0].text


def test_a_heading_with_no_body_before_the_next_heading_is_not_emitted():
    text = "4.1 Eligibility\n4.2 Closing\nSubmit a written request.\n"

    blocks = chunk_page_into_rule_blocks(text, page=0)

    assert len(blocks) == 1
    assert blocks[0].section_path == ("Closing",)


def test_empty_text_yields_no_blocks():
    assert chunk_page_into_rule_blocks("", page=0) == []


def test_every_block_records_its_page_number():
    blocks = chunk_page_into_rule_blocks("4.1 Eligibility\nMust be 18.\n", page=7)

    assert blocks[0].page == 7


def test_document_level_chunking_processes_every_page():
    parsed = ParsedDocument(
        pages=[
            ParsedPage(page_number=0, text="4.1 Eligibility\nMust be 18.\n", tables=[]),
            ParsedPage(page_number=1, text="4.2 Closing\nSubmit a request.\n", tables=[]),
        ],
        sections=[], source_format="pdf",
    )

    blocks = chunk_document_into_rule_blocks(parsed)

    assert [b.page for b in blocks] == [0, 1]
    assert [b.section_number for b in blocks] == ["4.1", "4.2"]
