"""offline/documents/policy_chunker.py: rule-block chunking for non-finance
documents. Step 5.5 (2026-10-17): rewritten to find headings by how they
LOOK (bold, or a bigger font than body text), not by matching a number in
their own text -- a lone page-number footer matched a number pattern just
as well as a real heading did, which the first version of this module got
wrong against real documents. Also rewritten to let a rule block span a
page break, which the first version never allowed."""
from __future__ import annotations

from offline.documents.policy_chunker import chunk_document_into_rule_blocks, chunk_lines_into_rule_blocks
from offline.parsers.layout_parser import Line, ParsedDocument, ParsedPage

BODY_SIZE = 10.0
HEADING_SIZE = 14.0  # meaningfully bigger than body -- qualifies by size alone


def _body(text: str, top: float) -> Line:
    return Line(text=text, top=top, max_font_size=BODY_SIZE, is_bold=False)


def _heading(text: str, top: float, bold: bool = True, size: float = BODY_SIZE) -> Line:
    return Line(text=text, top=top, max_font_size=size, is_bold=bold)


def _on_page(lines: list[Line], page: int = 0) -> list[tuple[Line, int]]:
    return [(line, page) for line in lines]


def test_a_bold_numbered_clause_becomes_one_block():
    lines = [
        _heading("4.2.1 Account Eligibility for Minors", top=0),
        _body("Applicants under the age of 18 years are permitted to open a custodial", top=12),
        _body("savings account provided a guardian is designated as a joint holder.", top=24),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1
    assert blocks[0].section_number == "4.2.1"
    assert "custodial" in blocks[0].text


def test_a_bigger_font_heading_with_no_bold_and_no_number_is_still_recognized():
    # several body lines, same size, so that size is genuinely the
    # document's dominant/body size -- a single body line would tie with
    # the heading's own size and make the "which size is body text" check
    # meaningless, the same way it would in a real, too-short document.
    lines = [
        _heading("Eligibility", top=0, bold=False, size=HEADING_SIZE),
        _body("Applicants must be 18 or older.", top=12),
        _body("A valid government-issued ID is required.", top=24),
        _body("Proof of address must be provided within 30 days.", top=36),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1
    assert blocks[0].section_number is None
    assert blocks[0].section_path == ("Eligibility",)


def test_a_bold_sentence_too_long_to_be_a_heading_is_treated_as_body():
    lines = [
        _heading("4.1 Eligibility", top=0),
        _heading("x" * 150, top=12, bold=True),  # a bold sentence, not a heading -- too long
        _body("Must be 18.", top=24),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1
    assert "x" * 150 in blocks[0].text


def test_a_single_decorative_glyph_is_never_a_heading_even_at_a_huge_size():
    """Real bug found against a real document: a lone bullet-like glyph
    ('o') at 2.5x body size satisfied the size check and was mistaken for
    a heading titled "o"."""
    lines = [
        _heading("4.1 Eligibility", top=0),
        _body("Must be 18.", top=12),
        _heading("o", top=24, bold=False, size=BODY_SIZE * 3),  # a decorative glyph, not a heading
        _body("Submit a request.", top=36),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1
    assert "Submit a request." in blocks[0].text  # stayed in the still-open "Eligibility" block


def test_a_lone_page_number_is_never_a_heading_even_if_bold():
    """The real bug this step fixes: a page-number footer matches a
    numbering pattern just as well as a real heading, however it happens
    to be styled -- excluded outright, not by luck of styling."""
    lines = [
        _heading("4.1 Eligibility", top=0),
        _body("Must be 18.", top=12),
        _heading("2", top=24, bold=True, size=HEADING_SIZE),  # a bold, big page-number footer
        _body("4.2 Closing", top=36),
    ]
    # rewrite "4.2 Closing" as a real heading too, for the next block
    lines[3] = _heading("4.2 Closing", top=36)
    lines.append(_body("Submit a request.", top=48))

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 2
    assert "2" not in [b.text for b in blocks]
    for b in blocks:
        assert b.text != "2"


def test_a_table_of_contents_entry_with_a_dot_leader_is_dropped_not_emitted():
    """Real production symptom this step also fixes: a table of contents
    repeats the real headings almost verbatim, with a dot-leader before
    the page number ("1. OBJECTIVES.......... 4") -- plausible-looking
    text that would otherwise have been stored as a "fact" about nothing."""
    lines = [
        _heading("PART ONE - OBJECTIVES AND SCOPE", top=0),
        _body("1. OBJECTIVES......................................... 4", top=12),
        _body("2. SCOPE.............................................. 4", top=24),
        _heading("4.1 Eligibility", top=36),
        _body("Must be 18.", top=48),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1  # the TOC heading's body was entirely dot-leader lines -- dropped
    # "4.1" (depth 2) nests inside the still-open "PART ONE" (depth 1) --
    # correct, since nothing closed that heading; only its own (TOC) body was dropped.
    assert blocks[0].section_path == ("PART ONE - OBJECTIVES AND SCOPE", "Eligibility")


def test_a_stray_short_numeric_fragment_between_headings_is_dropped_not_emitted():
    """Real production symptom this step fixes: a page-number footer that
    leaks in as the ENTIRE body of what would otherwise be its own
    fact -- dropped, not stored as a one-character "rule"."""
    lines = [
        _heading("4.1 Eligibility", top=0),
        _body("3", top=12),  # a stray footer, the whole "body" for this heading
        _heading("4.2 Closing", top=24),
        _body("Submit a written request to close the account.", top=36),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1
    assert blocks[0].section_path == ("Closing",)


def test_heading_hierarchy_is_preserved_in_section_path():
    lines = [
        _heading("4 Accounts", top=0),
        _heading("4.2 Eligibility", top=12),
        _heading("4.2.1 Minors", top=24),
        _body("Minors may open an account with a guardian.", top=36),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1
    assert blocks[0].section_path == ("Accounts", "Eligibility", "Minors")


def test_two_sibling_sub_clauses_do_not_bleed_into_each_other():
    lines = [
        _heading("4.2.1 Minors", top=0),
        _body("Minors may open an account with a guardian.", top=12),
        _heading("4.2.2 Seniors", top=24),
        _body("Seniors receive a fee waiver.", top=36),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 2
    assert blocks[0].section_path == ("Minors",)
    assert "guardian" in blocks[0].text
    assert blocks[1].section_path == ("Seniors",)
    assert "fee waiver" in blocks[1].text
    assert "guardian" not in blocks[1].text


def test_returning_to_a_shallower_heading_pops_the_deeper_ones():
    lines = [
        _heading("4 Accounts", top=0),
        _heading("4.1 Opening", top=12),
        _heading("4.1.1 Documents Required", top=24),
        _body("Bring a valid ID.", top=36),
        _heading("4.2 Closing", top=48),
        _body("Submit a written request.", top=60),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 2
    assert blocks[0].section_path == ("Accounts", "Opening", "Documents Required")
    assert blocks[1].section_path == ("Accounts", "Closing")  # not ("Accounts", "Opening", "Closing")


def test_named_section_style_heading_is_recognized():
    lines = [
        _heading("Section 4.2 - Eligibility", top=0),
        _body("Applicants must be 18 or older.", top=12),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1
    assert blocks[0].section_number == "4.2"
    assert blocks[0].section_path == ("Eligibility",)


def test_text_before_the_first_heading_is_dropped_it_has_no_section_to_cite():
    lines = [
        _body("This document was last reviewed in January.", top=0),
        _heading("4.1 Eligibility", top=12),
        _body("Must be 18.", top=24),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1
    assert "last reviewed" not in blocks[0].text


def test_a_heading_with_no_real_body_before_the_next_heading_is_not_emitted():
    lines = [
        _heading("4.1 Eligibility", top=0),
        _heading("4.2 Closing", top=12),
        _body("Submit a written request.", top=24),
    ]

    blocks = chunk_lines_into_rule_blocks(_on_page(lines))

    assert len(blocks) == 1
    assert blocks[0].section_path == ("Closing",)


def test_empty_lines_yield_no_blocks():
    assert chunk_lines_into_rule_blocks([]) == []


def test_every_block_records_the_page_its_own_heading_started_on():
    blocks = chunk_lines_into_rule_blocks(_on_page([_heading("4.1 Eligibility", top=0), _body("Must be 18.", top=12)], page=7))

    assert blocks[0].page == 7


# --- the real fix: a rule block can now span a page break ---


def test_a_rule_block_spans_a_page_break():
    """The real bug this step fixes: a heading still open at the end of a
    page must stay open into the next page's body, instead of being cut
    off because each page used to be chunked in isolation."""
    page_0 = [_heading("4.1 Eligibility", top=0), _body("Applicants must be at least 18 years of age", top=12)]
    page_1 = [_body("and provide a valid government-issued ID.", top=0)]

    blocks = chunk_lines_into_rule_blocks(_on_page(page_0, page=0) + _on_page(page_1, page=1))

    assert len(blocks) == 1
    assert "18 years of age" in blocks[0].text
    assert "valid government-issued ID" in blocks[0].text
    assert blocks[0].page == 0  # cited by the page its heading started on, not where it ends


def test_a_new_heading_on_the_next_page_still_closes_the_previous_blocks_body():
    page_0 = [_heading("4.1 Eligibility", top=0), _body("Must be 18.", top=12)]
    page_1 = [_heading("4.2 Closing", top=0), _body("Submit a written request.", top=12)]

    blocks = chunk_lines_into_rule_blocks(_on_page(page_0, page=0) + _on_page(page_1, page=1))

    assert len(blocks) == 2
    assert blocks[0].page == 0
    assert blocks[1].page == 1


# --- document-level chunking, through the real ParsedDocument shape ---


def test_document_level_chunking_processes_every_page_and_spans_breaks():
    parsed = ParsedDocument(
        pages=[
            ParsedPage(page_number=0, text="", tables=[], lines=(_heading("4.1 Eligibility", top=0), _body("Applicants must be at least 18", top=12))),
            ParsedPage(page_number=1, text="", tables=[], lines=(_body("years of age.", top=0), _heading("4.2 Closing", top=12), _body("Submit a request.", top=24))),
        ],
        sections=[], source_format="pdf",
    )

    blocks = chunk_document_into_rule_blocks(parsed)

    assert [b.section_number for b in blocks] == ["4.1", "4.2"]
    assert "18" in blocks[0].text and "years of age" in blocks[0].text
    assert blocks[0].page == 0
    assert blocks[1].page == 1


# --- .txt fallback: no font information exists at all, so the original
# text-pattern approach is kept as the only available signal ---


def test_txt_fallback_recognizes_a_numbered_heading_with_no_font_info():
    parsed = ParsedDocument(
        pages=[ParsedPage(page_number=0, text="4.2.1 Minors\nMinors may open an account with a guardian.\n", tables=[], lines=())],
        sections=[], source_format="txt",
    )

    blocks = chunk_document_into_rule_blocks(parsed)

    assert len(blocks) == 1
    assert blocks[0].section_number == "4.2.1"
    assert blocks[0].section_path == ("Minors",)


def test_txt_fallback_text_before_first_heading_is_dropped():
    parsed = ParsedDocument(
        pages=[ParsedPage(page_number=0, text="Last reviewed in January.\n4.1 Eligibility\nMust be 18.\n", tables=[], lines=())],
        sections=[], source_format="txt",
    )

    blocks = chunk_document_into_rule_blocks(parsed)

    assert len(blocks) == 1
    assert "reviewed" not in blocks[0].text
