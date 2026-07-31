"""Layout/table parser tests."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from offline.parsers.layout_parser import _strip_null_bytes, parse_document

PDF_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "path_a" / "sample.pdf"


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
    """The whole point of choosing pdfplumber over a plain text extractor:
    a table must come back as rows-of-cells, with each figure still
    associated with its row label and column header — not merged into an
    ambiguous blob of numbers."""
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
    """Regression, 2026-07-25: found building the real 5-document index.
    Some real 10-K/10-Q PDFs use a subset font where a dash or trademark
    glyph decodes to a literal U+0000 ("Post-it\\x00ae-solutions" for
    "Post-it(R)-solutions") rather than its real codepoint. PostgreSQL's
    JSONB column hard-rejects U+0000, and it would otherwise abort the
    entire index build at the final persist step -- after every LLM call
    for every document has already run."""

    def test_strip_null_bytes_removes_the_byte(self):
        assert _strip_null_bytes("Post-it\x00ae-solutions") == "Post-itae-solutions"
        assert _strip_null_bytes("$ \x00.") == "$ ."

    def test_strip_null_bytes_is_a_no_op_on_clean_text(self):
        assert _strip_null_bytes("Net sales were $32.2 billion.") == "Net sales were $32.2 billion."

    def test_parse_pdf_strips_null_bytes_from_page_text(self, monkeypatch):
        fake_page = SimpleNamespace(
            extract_text=lambda: "Net sales grew due to Post-it\x00ae-solutions demand.",
            extract_tables=lambda: [],
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
