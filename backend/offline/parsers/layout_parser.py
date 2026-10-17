"""Layout- and table-aware parser — Path A. No model involved.

Format-driven, not content-driven: plain text passes through, table
logic just doesn't fire when there are no tables.

PDF parsing uses pdfplumber (pinned in pyproject.toml) — pure Python,
ruling-line table extraction, no external binary, and it keeps row/column
structure the numeric extractor needs instead of flattening tables to text.

Pages are zero-indexed to match the eval data's convention.

HTML/DOCX/PPTX deferred (corpus is PDF-only, never exercises them).
CSV/XLSX/XBRL route to Path B instead — parse_document rejects them here
rather than mis-parsing structured data as prose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union

Table = tuple[tuple[str, ...], ...]  # a table: a tuple of rows, each a tuple of cells

_PATH_B_FORMATS = {".csv", ".xlsx", ".xbrl"}
_UNIMPLEMENTED_FORMATS = {".html", ".htm", ".docx", ".pptx"}

# Crude heuristic for plain-text headers: short line, no terminal
# punctuation. Refine once real filing text, not just fixtures, is in scope.
_SECTION_HEADER_RE = re.compile(r"^(?P<title>[A-Z][A-Za-z0-9 ,&/\-]{2,80})$")


@dataclass(frozen=True)
class SectionBoundary:
    title: str
    start_offset: int


@dataclass(frozen=True)
class ParsedPage:
    page_number: int  # zero-indexed
    text: str
    tables: list[Table]


@dataclass(frozen=True)
class ParsedDocument:
    pages: list[ParsedPage]
    sections: list[SectionBoundary]
    source_format: str

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def tables(self) -> list[Table]:
        return [table for page in self.pages for table in page.tables]


def parse_document(path: Union[str, Path]) -> ParsedDocument:
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in _PATH_B_FORMATS:
        raise ValueError(
            f"parse_document: {suffix} is structured input and belongs on "
            f"Path B (offline/parsers/schema_mapper.py), not the "
            f"layout/table parser: {path}"
        )
    if suffix in _UNIMPLEMENTED_FORMATS:
        raise NotImplementedError(
            f"parse_document: {suffix} parsing is not implemented yet — "
            f"picking a parser dependency for this format is deferred: {path}"
        )
    if suffix == ".pdf":
        return _parse_pdf(path)
    if suffix != ".txt":
        raise ValueError(f"parse_document: unrecognized format {suffix}: {path}")

    return _parse_txt(path)


def _parse_txt(path: Path) -> ParsedDocument:
    text = path.read_text(encoding="utf-8")
    sections = _find_section_boundaries(text)
    # No table reconstruction for .txt -- no ruling lines to key off of.
    # Fine for prose, a known gap for tabular .txt specifically.
    page = ParsedPage(page_number=0, text=text, tables=[])
    return ParsedDocument(pages=[page], sections=sections, source_format="txt")


def _strip_null_bytes(text: str) -> str:
    """Some 10-K/10-Q PDFs decode a dash/trademark glyph to literal U+0000
    (e.g. "Post-it\x00ae-solutions"). Postgres JSONB hard-rejects U+0000,
    which would otherwise abort the whole build after every LLM call already
    ran — stripped here rather than guessed at (e.g. reinserting "®")."""
    return text.replace("\x00", "")


def _parse_pdf(path: Path) -> ParsedDocument:
    import pdfplumber

    pages: list[ParsedPage] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages):  # zero-indexed
            text = _strip_null_bytes(page.extract_text() or "")
            tables: list[Table] = [
                tuple(tuple(_strip_null_bytes((cell or "").strip()) for cell in row) for row in raw_table)
                for raw_table in page.extract_tables()
            ]
            pages.append(ParsedPage(page_number=page_number, text=text, tables=tables))

    sections = _find_section_boundaries("\n".join(p.text for p in pages))
    return ParsedDocument(pages=pages, sections=sections, source_format="pdf")


def _find_section_boundaries(text: str) -> list[SectionBoundary]:
    sections: list[SectionBoundary] = []
    offset = 0
    for line in text.split("\n"):
        stripped = line.strip()
        match = _SECTION_HEADER_RE.match(stripped) if stripped else None
        if match:
            sections.append(SectionBoundary(title=match.group("title"), start_offset=offset))
        offset += len(line) + 1
    return sections
