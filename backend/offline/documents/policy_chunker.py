"""Splits a non-finance document's text into rule blocks -- whole clauses,
not sentences, so a rule survives together with its own conditions and
exceptions. Rules only, no model: headings and numbered clauses are found
by pattern, the heading hierarchy above each block is kept as its path.

Reuses offline/parsers/layout_parser.py's PDF/text extraction rather than
parsing files a second way; this module only adds structure on top of the
plain text it already gives back.

Known simplification: a rule block never spans a page break in this first
version -- each page's text is chunked on its own. Real policy documents,
once available, may show this needs revisiting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from offline.parsers.layout_parser import ParsedDocument

# "4.2.1 Account Eligibility for Minors" / "4.2 Eligibility:" -- depth is the
# number of dot-separated parts.
_NUMBERED_HEADING_RE = re.compile(
    r"^(?P<number>\d+(?:\.\d+)*)[.)]?\s+(?P<title>[A-Z][^\n]{2,100}?):?\s*$"
)
# "Section 4.2 - Eligibility" / "Section 4.2: Eligibility"
_NAMED_SECTION_RE = re.compile(
    r"^Section\s+(?P<number>\d+(?:\.\d+)*)\s*[-:]\s*(?P<title>[A-Z][^\n]{2,100}?):?\s*$"
)
# An unnumbered heading: short line, starts with a capital, no terminal
# punctuation -- always treated as a top-level heading (a known
# simplification: unnumbered sub-headings aren't told apart by depth).
_PLAIN_HEADING_RE = re.compile(r"^(?P<title>[A-Z][A-Za-z0-9 ,&/\-]{2,80})$")


@dataclass(frozen=True)
class RuleBlockDraft:
    """One rule block, before the antecedent/consequent/exception split.
    `page` is zero-indexed, matching the rest of the codebase."""
    text: str
    section_path: tuple[str, ...]
    section_number: Optional[str]
    page: int


@dataclass
class _HeadingStackEntry:
    depth: int
    title: str


def _match_heading(line: str) -> Optional[tuple[Optional[str], str, int]]:
    """Returns (number_or_None, title, depth) if `line` is a heading."""
    stripped = line.strip()
    if not stripped:
        return None
    match = _NUMBERED_HEADING_RE.match(stripped) or _NAMED_SECTION_RE.match(stripped)
    if match:
        number = match.group("number")
        depth = number.count(".") + 1
        return number, match.group("title").strip(), depth
    match = _PLAIN_HEADING_RE.match(stripped)
    if match:
        return None, match.group("title").strip(), 1
    return None


def _heading_path(stack: list[_HeadingStackEntry], title: str) -> tuple[str, ...]:
    return tuple(entry.title for entry in stack) + (title,)


def chunk_page_into_rule_blocks(text: str, page: int) -> list[RuleBlockDraft]:
    """Chunks one page's text. A heading starts a new block; everything up
    to the next heading is that block's body. Text before the first
    heading (a preamble) is dropped -- it has no section to cite."""
    stack: list[_HeadingStackEntry] = []
    blocks: list[RuleBlockDraft] = []
    current_number: Optional[str] = None
    current_path: tuple[str, ...] = ()
    current_body: list[str] = []

    def _flush() -> None:
        body = "\n".join(current_body).strip()
        if body and current_path:
            blocks.append(RuleBlockDraft(text=body, section_path=current_path, section_number=current_number, page=page))

    for line in text.split("\n"):
        heading = _match_heading(line)
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
    blocks: list[RuleBlockDraft] = []
    for page in parsed.pages:
        blocks.extend(chunk_page_into_rule_blocks(page.text, page.page_number))
    return blocks
