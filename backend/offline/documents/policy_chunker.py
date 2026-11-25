"""Splits a non-finance document's text into rule blocks -- whole clauses,
not sentences, so a rule survives together with its own conditions and
exceptions. No model: a heading is found by how it LOOKS (bold, or a
bigger font than the surrounding body text), not by matching a number in
its own text. A real document showed why that matters: a lone page-number
footer matches a numbering pattern exactly as well as a real heading
does, and the first version of this module (numbering as the gate)
treated stray footers and fragments as real content because of it.

When a number IS present in a heading's own text, it is still used -- but
only to help decide DEPTH (how deeply nested the heading is) once
something has already qualified as a heading by how it looks. Numbers are
a hint now, never the admission test.

A rule block can span a page break: whatever heading is still open at the
end of one page stays open into the next, instead of resetting. The first
version of this module never allowed that and cut real rules in half at
every page boundary -- also found against a real document, not assumed.

Reuses offline/parsers/layout_parser.py's PDF/text extraction rather than
parsing files a second way; this module only adds structure on top of the
plain text and lines it already gives back. For .txt input (no font
information exists there at all -- see ParsedPage.lines), the original
numbering/plain-heading text patterns are the only signal available and
are kept as a fallback.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional, Sequence

from offline.parsers.layout_parser import Line, ParsedDocument

# Used only to refine DEPTH on a line already admitted as a heading by how
# it looks (see module docstring) -- never to decide whether it is one.
_NUMBERED_HEADING_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)*)[.)]?\s+(?P<title>.+)$")
_NAMED_SECTION_RE = re.compile(r"^Section\s+(?P<number>\d+(?:\.\d+)*)\s*[-:]\s*(?P<title>.+)$")

# .txt fallback only -- no font/bold information exists for plain text, so
# this is the same text-pattern approach the whole module used to use.
_TXT_NUMBERED_HEADING_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)*)[.)]?\s+(?P<title>[A-Z][^\n]{2,100}?):?\s*$")
_TXT_NAMED_SECTION_RE = re.compile(r"^Section\s+(?P<number>\d+(?:\.\d+)*)\s*[-:]\s*(?P<title>[A-Z][^\n]{2,100}?):?\s*$")
_TXT_PLAIN_HEADING_RE = re.compile(r"^(?P<title>[A-Z][A-Za-z0-9 ,&/\-]{2,80})$")

_PURE_DIGIT_RE = re.compile(r"^\d+$")

# A heading's font must be at least this much bigger than the document's
# own body-text size to qualify on size alone (boldness qualifies at any
# size -- see _looks_like_heading).
_BODY_SIZE_FONT_RATIO = 1.15
# A bold line this long is a bold SENTENCE, not a heading.
_MAX_HEADING_LENGTH = 100
# A single decorative glyph (a large bullet character, in a real
# document) can be bigger than body text without being a heading at all --
# a real heading is always at least this many characters.
_MIN_HEADING_LENGTH = 3
# A flushed block shorter than this, with no letters at all, is a stray
# footer/page-number fragment that leaked into the body text, not a real
# rule -- dropped rather than emitted as its own fact (a real symptom
# found in production: lone "2", "3", "4" facts from page-number footers).
_MIN_REAL_BODY_LENGTH = 15
# A line with a dot-leader ("1. OBJECTIVES.......... 4") or the garbled
# repeated glyph a broken dot-leader sometimes decodes to is a table-of-
# contents entry, not a real rule's text -- found in a real document's
# own table of contents, which otherwise reads as plausible-looking body
# text (it repeats the real headings almost verbatim) and would have been
# stored as a "fact" about nothing.
_TOC_DOT_LEADER_RE = re.compile(r"\.{3,}|…{2,}")


@dataclass(frozen=True)
class RuleBlockDraft:
    """One rule block, before the antecedent/consequent/exception split.
    `page` is zero-indexed, matching the rest of the codebase, and is the
    page the block's OWN HEADING started on, even if its body continues
    onto later pages."""
    text: str
    section_path: tuple[str, ...]
    section_number: Optional[str]
    page: int


@dataclass
class _HeadingStackEntry:
    depth: int
    title: str


def _dominant_body_size(lines: Sequence[Line]) -> float:
    """The most common font size across the whole document -- the
    baseline "ordinary text" size a real heading must stand out from.
    Computed once per document, not per page, since a heading's size is
    meaningful relative to the WHOLE document's body text, not just
    whatever happens to share its own page."""
    sizes = [round(line.max_font_size, 1) for line in lines if line.text.strip()]
    if not sizes:
        return 0.0
    return Counter(sizes).most_common(1)[0][0]


def _looks_like_heading(line: Line, body_size: float) -> bool:
    stripped = line.text.strip()
    if len(stripped) < _MIN_HEADING_LENGTH or len(stripped) > _MAX_HEADING_LENGTH:
        return False
    if _PURE_DIGIT_RE.match(stripped):
        return False  # a lone number is never a heading, however it happens to be styled
    if body_size and line.max_font_size >= body_size * _BODY_SIZE_FONT_RATIO:
        return True
    return line.is_bold


def _parse_heading_text(stripped: str) -> tuple[Optional[str], str, int]:
    """Given text already confirmed to look like a heading, figures out
    its number (if any) and depth. A number present refines depth; no
    number means depth 1 -- the same documented simplification unnumbered
    headings always had (they aren't told apart by depth)."""
    match = _NUMBERED_HEADING_RE.match(stripped) or _NAMED_SECTION_RE.match(stripped)
    if match:
        number = match.group("number")
        return number, match.group("title").strip(), number.count(".") + 1
    return None, stripped, 1


def _heading_path(stack: list[_HeadingStackEntry], title: str) -> tuple[str, ...]:
    return tuple(entry.title for entry in stack) + (title,)


def _is_real_body(body: str) -> bool:
    return len(body) >= _MIN_REAL_BODY_LENGTH or any(c.isalpha() for c in body)


def chunk_lines_into_rule_blocks(lines_with_pages: Sequence[tuple[Line, int]]) -> list[RuleBlockDraft]:
    """The real, visual-heading chunker. Takes one continuous stream of
    (line, page_number) pairs spanning the WHOLE document -- not one page
    at a time -- so a heading still open at the end of a page carries
    straight into the next one instead of being cut off."""
    body_size = _dominant_body_size([line for line, _ in lines_with_pages])

    stack: list[_HeadingStackEntry] = []
    blocks: list[RuleBlockDraft] = []
    current_number: Optional[str] = None
    current_path: tuple[str, ...] = ()
    current_body: list[str] = []
    current_page: Optional[int] = None  # the page THIS block's own heading started on

    def _flush() -> None:
        body = "\n".join(current_body).strip()
        if body and current_path and _is_real_body(body):
            assert current_page is not None
            blocks.append(RuleBlockDraft(text=body, section_path=current_path, section_number=current_number, page=current_page))

    for line, page_number in lines_with_pages:
        if not _looks_like_heading(line, body_size):
            if not _TOC_DOT_LEADER_RE.search(line.text):
                current_body.append(line.text)
            continue
        _flush()
        number, title, depth = _parse_heading_text(line.text.strip())
        while stack and stack[-1].depth >= depth:
            stack.pop()
        current_path = _heading_path(stack, title)
        current_number = number
        current_body = []
        current_page = page_number
        stack.append(_HeadingStackEntry(depth=depth, title=title))

    _flush()
    return blocks


def _chunk_text_page_into_rule_blocks(text: str, page: int) -> list[RuleBlockDraft]:
    """The .txt fallback: no font information exists at all for plain
    text, so this is the original number/plain-heading text-pattern
    approach, unchanged -- still chunked one page at a time (plain .txt
    uploads are a single page in this project today, so cross-page
    continuity has never been exercised or needed here)."""
    stack: list[_HeadingStackEntry] = []
    blocks: list[RuleBlockDraft] = []
    current_number: Optional[str] = None
    current_path: tuple[str, ...] = ()
    current_body: list[str] = []

    def _match_txt_heading(line: str) -> Optional[tuple[Optional[str], str, int]]:
        stripped = line.strip()
        if not stripped:
            return None
        match = _TXT_NUMBERED_HEADING_RE.match(stripped) or _TXT_NAMED_SECTION_RE.match(stripped)
        if match:
            number = match.group("number")
            return number, match.group("title").strip(), number.count(".") + 1
        match = _TXT_PLAIN_HEADING_RE.match(stripped)
        if match:
            return None, match.group("title").strip(), 1
        return None

    def _flush() -> None:
        body = "\n".join(current_body).strip()
        if body and current_path:
            blocks.append(RuleBlockDraft(text=body, section_path=current_path, section_number=current_number, page=page))

    for line in text.split("\n"):
        heading = _match_txt_heading(line)
        if heading is None:
            current_body.append(line)
            continue
        _flush()
        number, title, depth = heading
        while stack and stack[-1].depth >= depth:
            stack.pop()
        current_path = _heading_path(stack, title)
        current_number = number
        current_body = []
        stack.append(_HeadingStackEntry(depth=depth, title=title))

    _flush()
    return blocks


def chunk_document_into_rule_blocks(parsed: ParsedDocument) -> list[RuleBlockDraft]:
    has_lines = any(page.lines for page in parsed.pages)
    if not has_lines:
        # .txt, or a PDF page with no extractable words at all -- fall
        # back to the text-pattern approach, per page (see
        # _chunk_text_page_into_rule_blocks's own note on why per-page is
        # fine there).
        blocks: list[RuleBlockDraft] = []
        for page in parsed.pages:
            blocks.extend(_chunk_text_page_into_rule_blocks(page.text, page.page_number))
        return blocks

    lines_with_pages = [(line, page.page_number) for page in parsed.pages for line in page.lines]
    return chunk_lines_into_rule_blocks(lines_with_pages)
