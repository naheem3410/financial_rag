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

Step 5.5 (2026-10-17): real non-finance PDFs exposed two things the first
84 financial filings never did -- a two-column layout that `extract_text()`
reads straight through, interleaving both columns into one garbled line,
and headings with no reliable numbering at all, where the only real signal
is that the heading is bold or in a bigger font. Column detection below is
deliberately conservative (a real gap, with a real amount of text solidly
on each side) so an ordinary single-column page -- every financial filing
parsed so far -- takes the exact same `page.extract_text()` path as
before, unchanged. `ParsedPage.lines` is new and additive: the numeric
extractor and everything else that already depends on `.text` is
untouched; only `policy_chunker.py` reads `.lines`, for the heading check
described above.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

Table = tuple[tuple[str, ...], ...]  # a table: a tuple of rows, each a tuple of cells

_PATH_B_FORMATS = {".csv", ".xlsx", ".xbrl"}
_UNIMPLEMENTED_FORMATS = {".html", ".htm", ".docx", ".pptx"}

# Crude heuristic for plain-text headers: short line, no terminal
# punctuation. Refine once real filing text, not just fixtures, is in scope.
_SECTION_HEADER_RE = re.compile(r"^(?P<title>[A-Z][A-Za-z0-9 ,&/\-]{2,80})$")

# Column-split tuning, each number found by testing against real documents
# rather than guessed, and checked against known non-columns (a table of
# contents, a real ruled table, an ordinary single-column page) so none of
# them is mistaken for a second column:
_HISTOGRAM_BIN_POINTS = 5.0
# A real gutter must be empty for at least this share of the page's own
# lines -- not literally every line (a stray word can drift into it), but
# not just a handful either.
_MIN_LINE_EMPTY_FRACTION = 0.9
# A real two-column layout splits the page roughly in half; a gap this far
# off-centre is something else (an indent after a short list number, in
# one real case) -- checked, not assumed.
_COLUMN_CENTER_MIN_FRACTION = 0.3
_COLUMN_CENTER_MAX_FRACTION = 0.7
_MIN_WORDS_PER_COLUMN = 10
# Each side must have real flowing-text lines, not just a trailing single
# token -- a table of contents (title, then just a page number) produced
# a gutter just as wide as a real second column, with zero such lines.
_MIN_MULTI_WORD_LINES_PER_COLUMN = 3
_MULTI_WORD_LINE_THRESHOLD = 3
# Words within this many points of vertical position are "the same line" --
# small enough that two genuinely stacked lines are never merged.
_LINE_TOP_TOLERANCE_POINTS = 2.0


@dataclass(frozen=True)
class SectionBoundary:
    title: str
    start_offset: int


@dataclass(frozen=True)
class Line:
    """One line of text on a PDF page, with just enough style information
    to tell a heading from body text without depending on its wording --
    a numbered line is not reliably a heading (a lone page-number footer
    matches a number pattern just as well); a bold or larger-than-body
    line almost always is."""
    text: str
    top: float  # vertical position on the page, for reading order
    max_font_size: float
    is_bold: bool


@dataclass(frozen=True)
class ParsedPage:
    page_number: int  # zero-indexed
    text: str
    tables: list[Table]
    # Only populated for PDFs -- a .txt file carries no font information at
    # all, so this stays empty there (policy_chunker.py falls back to its
    # own plain-text heading patterns in that case).
    lines: tuple[Line, ...] = ()


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


def _line_from_words(words: list[dict]) -> Line:
    ordered = sorted(words, key=lambda w: w["x0"])
    return Line(
        text=" ".join(w["text"] for w in ordered),
        top=min(w["top"] for w in words),
        max_font_size=max(w["size"] for w in words),
        is_bold=any("bold" in w["fontname"].lower() for w in words),
    )


def _group_words_into_lines(words: list[dict]) -> list[list[dict]]:
    """Groups raw pdfplumber words into lines by vertical position --
    needed both for reading order and for the column check below, which
    has to reason about whole lines, not individual word positions."""
    lines: list[list[dict]] = []
    current: list[dict] = []
    current_top: Optional[float] = None
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if current_top is not None and abs(w["top"] - current_top) > _LINE_TOP_TOLERANCE_POINTS:
            lines.append(current)
            current = []
            current_top = None
        current.append(w)
        if current_top is None:
            current_top = w["top"]
    if current:
        lines.append(current)
    return lines


def _words_to_lines(words: list[dict]) -> list[Line]:
    return [_line_from_words(group) for group in _group_words_into_lines(words)]


def _detect_column_split(words: list[dict], page_width: float, has_table: bool) -> Optional[float]:
    """Returns the x-coordinate splitting the page into two columns, or
    `None` if the page reads as one column.

    Checked against three real non-columns before trusting this, not just
    one real column, since each would otherwise have been mistaken for a
    second column by a simpler check: a page pdfplumber already resolved
    as a ruled table (its own column structure, not prose -- `has_table`
    skips this entirely); a table of contents (title, then just a page
    number -- a real, wide, centred gap, but with nothing resembling a
    second column of actual sentences on its far side); and an ordinary
    single-column page, where one word starting far from its neighbours
    can still open up a wide run in a naive gap-between-word-starts check.

    What actually holds up: a histogram of how many LINES (not individual
    words) have a word crossing each small band across the page. A
    genuine gutter is empty for nearly every line, not just the aggregate
    word count -- and it sits near the middle of the page, since real
    columns are roughly equal width, and each side still needs several
    lines of real, multi-word prose, not a single trailing token."""
    if len(words) < 2 * _MIN_WORDS_PER_COLUMN or has_table:
        return None

    lines = _group_words_into_lines(words)
    if len(lines) < 4:
        return None

    bin_count = int(page_width // _HISTOGRAM_BIN_POINTS) + 1
    lines_touching_bin = [0] * bin_count
    for line in lines:
        touched_bins: set[int] = set()
        for w in line:
            first_bin = int(w["x0"] // _HISTOGRAM_BIN_POINTS)
            last_bin = min(int(w["x1"] // _HISTOGRAM_BIN_POINTS), bin_count - 1)
            touched_bins.update(range(max(first_bin, 0), last_bin + 1))
        for b in touched_bins:
            lines_touching_bin[b] += 1

    empty_threshold = len(lines) * (1 - _MIN_LINE_EMPTY_FRACTION)
    best_width, best_start, best_end = 0.0, None, None
    run_start: Optional[int] = None
    for i, touching in enumerate(lines_touching_bin):
        if touching <= empty_threshold:
            if run_start is None:
                run_start = i
            continue
        if run_start is not None and run_start > 0:  # not the page's own left margin
            width = (i - run_start) * _HISTOGRAM_BIN_POINTS
            if width > best_width:
                best_width, best_start, best_end = width, run_start, i
        run_start = None
    # a run still open at the end of the page is the right margin, never counted

    if best_start is None:
        return None

    split_x = ((best_start + best_end) / 2) * _HISTOGRAM_BIN_POINTS
    center_fraction = split_x / page_width
    if not (_COLUMN_CENTER_MIN_FRACTION <= center_fraction <= _COLUMN_CENTER_MAX_FRACTION):
        return None

    left_words = [w for w in words if w["x0"] < split_x]
    right_words = [w for w in words if w["x0"] >= split_x]
    if len(left_words) < _MIN_WORDS_PER_COLUMN or len(right_words) < _MIN_WORDS_PER_COLUMN:
        return None

    def _multi_word_line_count(side_words: list[dict]) -> int:
        return sum(1 for line in _group_words_into_lines(side_words) if len(line) >= _MULTI_WORD_LINE_THRESHOLD)

    if (
        _multi_word_line_count(left_words) < _MIN_MULTI_WORD_LINES_PER_COLUMN
        or _multi_word_line_count(right_words) < _MIN_MULTI_WORD_LINES_PER_COLUMN
    ):
        return None

    return split_x


def _parse_pdf(path: Path) -> ParsedDocument:
    import pdfplumber

    pages: list[ParsedPage] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages):  # zero-indexed
            raw_tables = page.extract_tables()
            tables: list[Table] = [
                tuple(tuple(_strip_null_bytes((cell or "").strip()) for cell in row) for row in raw_table)
                for raw_table in raw_tables
            ]

            words = page.extract_words(extra_attrs=["fontname", "size"])
            split_x = _detect_column_split(words, page.width, has_table=bool(raw_tables))

            if split_x is None:
                # The common case -- every financial filing parsed so far --
                # unchanged from before this step: one page.extract_text() call.
                text = _strip_null_bytes(page.extract_text() or "")
                lines = tuple(_words_to_lines(words))
            else:
                # A real two-column page: read the left column in full,
                # then the right column, reusing pdfplumber's own
                # extract_text() per column rather than hand-joining words,
                # so spacing/line-break fidelity matches the single-column
                # case exactly.
                left = page.crop((0, 0, split_x, page.height))
                right = page.crop((split_x, 0, page.width, page.height))
                text = _strip_null_bytes((left.extract_text() or "") + "\n" + (right.extract_text() or ""))
                left_words = [w for w in words if w["x0"] < split_x]
                right_words = [w for w in words if w["x0"] >= split_x]
                lines = tuple(_words_to_lines(left_words) + _words_to_lines(right_words))

            pages.append(ParsedPage(page_number=page_number, text=text, tables=tables, lines=lines))

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
