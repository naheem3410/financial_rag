"""Content-based section exclusion — Item 1A "Risk Factors" skip, scoped to
the current metric-axis 5-document run.

Why this exists, and why it's temporary: none of the current evaluation
questions have gold evidence in Risk Factors. That section is dense
narrative prose that (a) is a proven source of OpenAI content-filter false
positives and (b) would otherwise burn LLM budget segmenting text no
question needs. Skipping it is a run-scoped optimization, not a permanent
design — the general solution for risk-oriented queries is a metadata
section-router: tag propositions with their section and filter by query
type at retrieval time, never drop a section at index time. A future run
covering risk-oriented questions must revisit this.

Content-based, not gold-based (hard requirement): detection here must be
blind to which pages hold gold evidence — it operates purely on section
heading text. The caller is responsible for the separate gold-overlap
safety assertion (cross-checking skipped pages against gold evidence pages)
that makes this skip auditable; that check lives in the run script, not
here, because this module must not import or reference gold data at all.

Heading detection, calibrated against real 10-K text (pdfplumber
extraction). Two false-positive sources were found and must be rejected,
not just the true heading matched:

1. Table-of-contents entries: "ITEM 1A Risk Factors 10" — heading text
   immediately followed by a page number. Rejected by checking the first
   non-space character after the match is not a digit.
2. Narrative cross-references: "...incorporated by reference from Part I,
   Item 1A, 'Risk Factors,' of this document" — heading text embedded in a
   sentence, preceded by a comma. Rejected by checking the last non-space
   character before the match is not a comma.

Real 10-K text extracted via pdfplumber also carries kerning artifacts —
stray spaces inserted mid-word by the PDF's font rendering (observed:
"Propertie s.", "Comment s."). Patterns tolerate an optional space before a
trailing "s" for this reason.

The real Item 1A heading was also confirmed to appear **mid-page**, not at
a page's start (on 3M_2018_10K it is ~1100 characters into page 9, after a
forward-looking-statements paragraph) — detection searches full page text,
never a prefix.

**Page-level granularity trade-off.** The section end boundary is the page
on which Item 1B / Item 2 / Item 3 / Item 4 first appears (whichever comes
first — filers vary in whether they give Item 1B its own heading). That
boundary page is kept, not skipped, because on real filings it commonly
carries other items' substantive content (on 3M_2018_10K, Items 1B, 2, 3,
and 4 are all packed onto a single page) that must not be discarded to
remove a small amount of trailing Risk Factors text sharing that page.
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
    """First match of `pattern` in `text` that is neither a table-of-contents
    entry (heading immediately followed by a page number) nor a narrative
    cross-reference (heading immediately preceded by a comma). See module
    docstring for the real-world examples this rejects.
    """
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
    """Locates the Item 1A "Risk Factors" section by heading text alone.
    Returns `None` if no genuine Item 1A heading is found (content-based —
    never consults gold evidence). `end_page_number` is exclusive: the page
    where Item 1B/2/3/4 first appears is kept, not skipped.
    """
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

    # Heading found but no end marker before the document runs out — skip
    # through the last page rather than leaving the section half-excluded.
    return SkippedSection(
        reason="risk_factors_item_1a",
        start_page_number=start_page_number,
        end_page_number=pages[-1].page_number + 1,
    )


def skip_risk_factors_section(
    pages: Sequence[ParsedPage],
) -> tuple[list[ParsedPage], dict[int, SkippedSection]]:
    """Returns `(kept_pages, skipped_by_page_number)`. `skipped_by_page_number`
    maps every excluded page number to the `SkippedSection` it belongs to, so
    callers can log per-page skip reasons in the build manifest and run the
    gold-overlap safety assertion.
    """
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
