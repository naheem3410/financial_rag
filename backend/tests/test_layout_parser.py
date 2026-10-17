"""Layout/table parser tests."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from offline.parsers.layout_parser import _detect_column_split, _strip_null_bytes, _words_to_lines, parse_document

PDF_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "path_a" / "sample.pdf"
PAGE_WIDTH = 595.0  # A4-ish, matches the real documents this was calibrated against


def test_plain_prose_has_no_tables(tmp_path):
    src = tmp_path / "prose.txt"
    src.write_text("3M Company reported net sales of $32.2 billion for fiscal year 2018.", encoding="utf-8")

    parsed = parse_document(src)

    assert parsed.tables == []
    assert parsed.source_format == "txt"


def test_section_headers_are_detected(tmp_path):
    text = (
        "Management Discussion\n"
        "\n"
        "3M Company reported net sales of $32.2 billion for fiscal year 2018.\n"
        "\n"
        "Consolidated Statement of Cash Flows\n"
        "\n"
        "Operating activities generated $6.4 billion in cash.\n"
    )
    src = tmp_path / "sectioned.txt"
    src.write_text(text, encoding="utf-8")

    parsed = parse_document(src)

    titles = [s.title for s in parsed.sections]
    assert "Management Discussion" in titles
    assert "Consolidated Statement of Cash Flows" in titles


def test_csv_is_rejected_as_path_b_input(tmp_path):
    src = tmp_path / "data.csv"
    src.write_text("company,value\nAcme,1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Path B"):
        parse_document(src)


def test_html_is_not_yet_implemented(tmp_path):
    src = tmp_path / "filing.html"
    src.write_text("<html><body>fake</body></html>", encoding="utf-8")

    with pytest.raises(NotImplementedError, match="not implemented"):
        parse_document(src)


def test_pdf_pages_are_zero_indexed():
    parsed = parse_document(PDF_FIXTURE)

    assert [p.page_number for p in parsed.pages] == [0, 1]


def test_pdf_prose_page_text_is_extracted():
    parsed = parse_document(PDF_FIXTURE)

    assert "3M Company reported net sales of $32.2 billion" in parsed.pages[0].text
    assert parsed.pages[0].tables == []


def test_pdf_table_structure_is_preserved_not_flattened():
    """Tables must come back as rows-of-cells with row/column labels intact,
    not flattened into a blob of numbers -- the reason we use pdfplumber."""
    parsed = parse_document(PDF_FIXTURE)

    tables = parsed.pages[1].tables
    assert len(tables) == 1
    table = tables[0]

    assert table[0] == ("Line Item", "FY2023", "FY2024")
    operating_margin_row = next(row for row in table if row[0] == "Operating margin")
    assert operating_margin_row == ("Operating margin", "16.5%", "18.2%")


def test_pdf_document_level_text_and_tables_concatenate_all_pages():
    parsed = parse_document(PDF_FIXTURE)

    assert "3M Company" in parsed.text
    assert "Operating margin" in parsed.text
    assert len(parsed.tables) == 1


class TestNullByteSanitization:
    """Regression (2026-07-25): some 10-K/10-Q PDFs decode a glyph to literal
    U+0000, which Postgres JSONB rejects and would abort the build post-LLM."""

    def test_strip_null_bytes_removes_the_byte(self):
        assert _strip_null_bytes("Post-it\x00ae-solutions") == "Post-itae-solutions"
        assert _strip_null_bytes("$ \x00.") == "$ ."

    def test_strip_null_bytes_is_a_no_op_on_clean_text(self):
        assert _strip_null_bytes("Net sales were $32.2 billion.") == "Net sales were $32.2 billion."

    def test_parse_pdf_strips_null_bytes_from_page_text(self, monkeypatch):
        fake_page = SimpleNamespace(
            extract_text=lambda: "Net sales grew due to Post-it\x00ae-solutions demand.",
            extract_tables=lambda: [],
            extract_words=lambda extra_attrs=None: [],  # no words -- single-column fallback path
            width=612.0, height=792.0,
        )

        @contextmanager
        def _fake_open(path):
            yield SimpleNamespace(pages=[fake_page])

        monkeypatch.setattr("pdfplumber.open", _fake_open)

        parsed = parse_document(Path("fake.pdf"))

        assert "\x00" not in parsed.pages[0].text
        assert parsed.pages[0].text == "Net sales grew due to Post-itae-solutions demand."

    def test_parse_pdf_strips_null_bytes_from_table_cells(self, monkeypatch):
        fake_page = SimpleNamespace(
            extract_text=lambda: "",
            extract_tables=lambda: [[["Metric", "Value"], ["Pension benefit", "$ \x00."]]],
            extract_words=lambda extra_attrs=None: [],
            width=612.0, height=792.0,
        )

        @contextmanager
        def _fake_open(path):
            yield SimpleNamespace(pages=[fake_page])

        monkeypatch.setattr("pdfplumber.open", _fake_open)

        parsed = parse_document(Path("fake.pdf"))

        assert parsed.pages[0].tables[0][1] == ("Pension benefit", "$ .")


def test_pdf_sections_are_detected_across_pages():
    parsed = parse_document(PDF_FIXTURE)

    titles = [s.title for s in parsed.sections]
    assert "Management Discussion and Analysis" in titles
    assert "Consolidated Statement of Operations" in titles


# --- step 5.5 (2026-10-17): column detection, calibrated against real
# documents, not guessed -- see offline/parsers/layout_parser.py's own
# module docstring for what each real case below is standing in for. ---


def _word(text: str, x0: float, top: float, size: float = 10.0, bold: bool = False) -> dict:
    return {
        "text": text, "x0": x0, "x1": x0 + len(text) * 6.0, "top": top,
        "size": size, "fontname": "Arial-BoldMT" if bold else "ArialMT",
    }


def _column(x0: float, n_lines: int, words_per_line: int = 5, top_start: float = 50.0) -> list[dict]:
    words = []
    for i in range(n_lines):
        top = top_start + i * 12.0
        for j in range(words_per_line):
            words.append(_word(f"w{i}_{j}", x0 + j * 30.0, top))
    return words


def test_a_genuine_two_column_layout_is_detected():
    # left column ends around x=170, right column starts at x=320 --
    # gutter midpoint ~245, which is 41% of a 595-wide page, well centred.
    words = _column(x0=20.0, n_lines=10) + _column(x0=320.0, n_lines=10)

    split = _detect_column_split(words, PAGE_WIDTH, has_table=False)

    assert split is not None
    assert 170.0 < split < 320.0


def test_an_ordinary_single_column_page_is_never_split():
    """Lines spanning the full page width, no real gutter anywhere --
    the common case, every financial filing parsed so far."""
    words = []
    for i in range(10):
        top = 50.0 + i * 12.0
        for j in range(18):  # words spread across the whole width
            words.append(_word(f"w{i}_{j}", 20.0 + j * 30.0, top))

    assert _detect_column_split(words, PAGE_WIDTH, has_table=False) is None


def test_a_page_pdfplumber_already_resolved_as_a_table_is_never_split():
    """A real ruled table (e.g. Annexure D) has its own column structure,
    already handled separately -- this must never also try to text-split it."""
    words = _column(x0=20.0, n_lines=10) + _column(x0=320.0, n_lines=10)

    assert _detect_column_split(words, PAGE_WIDTH, has_table=True) is None


def test_a_table_of_contents_is_never_split():
    """Real case: a title column plus a single trailing page-number token
    per line produces a gap just as wide as a genuine second column, but
    with nothing resembling real prose on its far side."""
    words = _column(x0=20.0, n_lines=10, words_per_line=4)  # titles
    for i in range(10):
        words.append(_word(str(28 + i), 500.0, 50.0 + i * 12.0))  # one page number per line

    assert _detect_column_split(words, PAGE_WIDTH, has_table=False) is None


def test_an_off_center_gap_is_never_treated_as_a_column():
    """Real case: an indent after a short list number opened a gap as wide
    as a real gutter, but nowhere near the middle of the page."""
    words = _column(x0=20.0, n_lines=10, words_per_line=2)  # a short left label, e.g. "5.5.3"
    words += _column(x0=110.0, n_lines=10, words_per_line=10)  # the real body text, off to the right

    assert _detect_column_split(words, PAGE_WIDTH, has_table=False) is None


def test_too_few_words_overall_is_never_split():
    words = _column(x0=20.0, n_lines=1, words_per_line=2)

    assert _detect_column_split(words, PAGE_WIDTH, has_table=False) is None


def test_words_to_lines_reads_a_two_column_page_left_then_right():
    """The whole point of detecting the split: reading order comes out
    left-column-top-to-bottom, then right-column-top-to-bottom, not
    interleaved by vertical position the way a naive top-to-bottom read
    of the raw words would."""
    left = _column(x0=20.0, n_lines=5, words_per_line=3)
    right = _column(x0=320.0, n_lines=5, words_per_line=3)
    split = _detect_column_split(left + right, PAGE_WIDTH, has_table=False)
    assert split is not None

    left_words = [w for w in (left + right) if w["x0"] < split]
    right_words = [w for w in (left + right) if w["x0"] >= split]
    lines = _words_to_lines(left_words) + _words_to_lines(right_words)

    expected_line = ["w0_0 w0_1 w0_2", "w1_0 w1_1 w1_2", "w2_0 w2_1 w2_2", "w3_0 w3_1 w3_2", "w4_0 w4_1 w4_2"]
    assert [l.text for l in lines[:5]] == expected_line
    assert [l.text for l in lines[5:]] == expected_line
