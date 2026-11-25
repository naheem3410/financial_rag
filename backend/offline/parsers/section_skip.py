"""Content-based section exclusion — Item 1A "Risk Factors" skip, scoped
to the current metric-axis 5-document run.

Temporary: no current eval question has gold evidence in Risk Factors,
and that section both trips content-filter false positives and burns LLM
budget on unneeded text. A future run covering risk questions should
replace this with a metadata section-router instead of an index-time drop.

Content-based, not gold-based (hard requirement): detection is blind to
gold evidence, heading text only. The gold-overlap safety check that makes
this auditable lives in the run script, not here.

Heading detection is calibrated against real pdfplumber-extracted 10-K
text and rejects two false positives: a TOC entry (heading immediately
followed by a page number) and a narrative cross-reference (heading
preceded by a comma). Patterns also tolerate a stray kerning space before
a trailing "s" (pdfplumber artifact, e.g. "Propertie s.").

The real Item 1A heading can appear mid-page (not at page start), so
detection searches full page text, never a prefix.

Section end boundary is the page where Item 1B/2/3/4 first appears; that
page is kept, not skipped, since it commonly carries other items'
substantive content sharing the page with trailing Risk Factors text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from offline.parsers.layout_parser import ParsedPage

_ITEM_1A_PATTERN = re.compile(r"Item\s+1A\.?\s+Risk\s*Factor\s*s\.?", re.IGNORECASE)
_SECTION_END_PATTERNS = (
    re.compile(r"Item\s+1B\.?\s+Unresolved\s*Staff\s*Comment\s*s\.?", re.IGNORECASE),
    re.compile(r"Item\s+2\.?\s+Propert(?:y|ie\s*s)\.?", re.IGNORECASE),
    re.compile(r"Item\s+3\.?\s+Legal\s*Proceeding\s*s\.?", re.IGNORECASE),
    re.compile(r"Item\s+4\.?\s+Mine\s*Safety\s*Disclosure\s*s\.?", re.IGNORECASE),
)


def _find_real_heading(text: str, pattern: re.Pattern[str]) -> Optional[re.Match[str]]:
    """First match that's neither a TOC entry (heading followed by a page
    number) nor a cross-reference (heading preceded by a comma)."""
    for match in pattern.finditer(text):
        after = text[match.end() : match.end() + 5].lstrip()
        if after[:1].isdigit():
            continue
        before = text[max(0, match.start() - 3) : match.start()].rstrip()
        if before.endswith(","):
            continue
        return match
    return None


@dataclass(frozen=True)
class SkippedSection:
    reason: str
    start_page_number: int
    end_page_number: int  # exclusive — this page is kept, see module docstring


def find_risk_factors_section(pages: Sequence[ParsedPage]) -> Optional[SkippedSection]:
    """Locates Item 1A by heading text alone (content-based, never gold).
    end_page_number is exclusive — that page is kept, not skipped."""
    start_page_number: Optional[int] = None
    for page in pages:
        if _find_real_heading(page.text, _ITEM_1A_PATTERN) is not None:
            start_page_number = page.page_number
            break
    if start_page_number is None:
        return None

    for page in pages:
        if page.page_number <= start_page_number:
            continue
        if any(_find_real_heading(page.text, pattern) is not None for pattern in _SECTION_END_PATTERNS):
            return SkippedSection(
                reason="risk_factors_item_1a",
                start_page_number=start_page_number,
                end_page_number=page.page_number,
            )

    # No end marker found -- skip through the last page rather than
    # leaving the section half-excluded.
    return SkippedSection(
        reason="risk_factors_item_1a",
        start_page_number=start_page_number,
        end_page_number=pages[-1].page_number + 1,
    )


def skip_risk_factors_section(
    pages: Sequence[ParsedPage],
) -> tuple[list[ParsedPage], dict[int, SkippedSection]]:
    """Returns (kept_pages, skipped_by_page_number), letting callers log
    per-page skip reasons and run the gold-overlap check."""
    section = find_risk_factors_section(pages)
    if section is None:
        return list(pages), {}

    kept: list[ParsedPage] = []
    skipped: dict[int, SkippedSection] = {}
    for page in pages:
        if section.start_page_number <= page.page_number < section.end_page_number:
            skipped[page.page_number] = section
        else:
            kept.append(page)
    return kept, skipped
