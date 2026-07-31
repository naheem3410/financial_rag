"""Layout- and table-aware parser — Path A.

No model involved. Parsing is driven by file format, not content: plain
text passes straight through and table-reconstruction logic simply never
fires when there are no tables. A document containing no financial records
is handled correctly and produces ordinary prose text.

PDF parsing uses `pdfplumber`, pinned in pyproject.toml. Financial values
live in tables, and a parser that flattens a table to plain text destroys
the row/column association the numeric extractor and everything downstream
depend on — table structure needs to be preserved, not just "text
extracted". `pdfplumber` is pure Python, has solid ruling-line-based table
extraction that matches how tables are actually drawn in digital-native SEC
filings, and needs no external binary. Kept behind this module's
`parse_document` interface so it's swappable without touching call sites.

Pages are zero-indexed (`ParsedPage.page_number`), matching the evaluation
data's page-numbering convention so downstream page matching never has to
translate between conventions.

Still deferred: HTML/DOCX/PPTX each need their own parsing dependency
chosen — deferred rather than guessed at, since the current corpus is
PDF-only and never exercises them. CSV/XLSX/XBRL are explicitly not parsed
here: they route to Path B (offline/parsers/schema_mapper.py) instead, so
`parse_document` rejects them rather than mis-parsing structured data as
prose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union

Table = tuple[tuple[str, ...], ...]  # a table: a tuple of rows, each a tuple of cells

_PATH_B_FORMATS = {".csv", ".xlsx", ".xbrl"}
_UNIMPLEMENTED_FORMATS = {".html", ".htm", ".docx", ".pptx"}

# A crude prose section-header heuristic for plain text: a short line with
# no terminal sentence punctuation. Refine once real filing text (not just
# synthetic fixtures) is in scope.
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
    # Plain-text table reconstruction is not implemented: unlike PDF, .txt
    # carries no structural markup (ruling lines) a table detector could
    # rely on without heavy heuristics. Table logic simply does not fire
    # here -- fine for prose input, a known gap for tabular .txt specifically.
    page = ParsedPage(page_number=0, text=text, tables=[])
    return ParsedDocument(pages=[page], sections=sections, source_format="txt")


def _strip_null_bytes(text: str) -> str:
    """A small number of real 10-K/10-Q PDFs use a custom/subset font where
    a dash or trademark-symbol glyph decodes to a literal U+0000 rather than
    its real Unicode codepoint (e.g. "Post-it\x00ae-solutions" for
    "Post-it®-solutions"). PostgreSQL's JSONB type hard-rejects U+0000
    outright, so a single such page anywhere in the corpus would otherwise
    abort the entire index build at the final persist step, after every LLM
    call for every document has already run. Stripped here, at the source,
    rather than guessed at (e.g. reinserting "®") — the numeric-verbatim
    guard and numeric extractor already correctly treat text built from
    such a glyph as unextractable, so nothing here fabricates a value; it
    only removes a byte no downstream consumer can use anyway.
    """
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
