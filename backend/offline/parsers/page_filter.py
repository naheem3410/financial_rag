"""Content-based page filter — Path A.

Skips pages with no citable financial propositions (cover, TOC, signatures,
exhibit index, certifications) before segmentation, cutting cost and noise.

Content-based only, never gold-based — filtering on gold labels would leak
eval data into indexing and invalidate the retrieval metrics.

Two false-positive traps handled: (1) EDGAR 10-Ks print "Table of Contents"
as a running header on every page, so density of "Item N." entries is used
instead of a bare phrase match. (2) "certification" appears in ordinary
legal narrative ("class certification"), so only page-specific SOX
boilerplate patterns are matched, not the bare word.
"""
from __future__ import annotations

import re
from typing import Optional

from offline.parsers.layout_parser import ParsedPage

SkipReason = str  # "certification" | "exhibit_index" | "table_of_contents" | "signatures" | "cover"

_COVER_PATTERNS = (
    re.compile(r"\bUNITED STATES\b[\s\S]{0,80}\bSECURITIES AND EXCHANGE COMMISSION\b", re.IGNORECASE),
    re.compile(r"\bCommission File Number\b", re.IGNORECASE),
)
_SIGNATURE_PATTERNS = (
    re.compile(r"^\s*SIGNATURES?\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"pursuant to the requirements of Section 13", re.IGNORECASE),
    re.compile(r"/s/\s*\S+[\s\S]{0,60}/s/\s*\S+", re.IGNORECASE),  # 2+ signature lines
)
_EXHIBIT_INDEX_PATTERNS = (
    re.compile(r"\bEXHIBIT INDEX\b", re.IGNORECASE),
    re.compile(r"\bExhibit\s+No\.?\s+Description\b", re.IGNORECASE),
)
# Not a bare "CERTIFICATIONS?" match -- that hits ordinary legal narrative.
# Only SOX-specific boilerplate that actually appears on that page type.
_CERTIFICATION_PATTERNS = (
    re.compile(r"pursuant to (Section 302|18 U\.S\.C\. Section 1350)", re.IGNORECASE),
    re.compile(r"I,\s+[A-Z][a-zA-Z.\- ]+,\s+certify that", re.IGNORECASE),
)

_RULES: tuple[tuple[SkipReason, tuple["re.Pattern[str]", ...]], ...] = (
    ("certification", _CERTIFICATION_PATTERNS),
    ("exhibit_index", _EXHIBIT_INDEX_PATTERNS),
    ("signatures", _SIGNATURE_PATTERNS),
    ("cover", _COVER_PATTERNS),
)

# A real TOC lists ~14-16 SEC Items; a narrative page referencing one in
# passing does so once or twice. Density distinguishes them, not the phrase.
_ITEM_ENTRY_RE = re.compile(r"\bItem\s+\d+[A-Za-z]?\.?\s", re.IGNORECASE)
_MIN_ITEM_ENTRIES_FOR_TOC = 5


def classify_page(page: ParsedPage) -> Optional[SkipReason]:
    """A skip reason if the page matches a known non-substantive pattern,
    else None (segment normally)."""
    text = page.text

    if len(_ITEM_ENTRY_RE.findall(text)) >= _MIN_ITEM_ENTRIES_FOR_TOC:
        return "table_of_contents"

    for reason, patterns in _RULES:
        if any(p.search(text) for p in patterns):
            return reason
    return None


def filter_pages(pages: list[ParsedPage]) -> tuple[list[ParsedPage], dict[int, SkipReason]]:
    """Splits pages into (kept, skipped); skipped maps page_number -> reason
    for reporting."""
    kept: list[ParsedPage] = []
    skipped: dict[int, SkipReason] = {}
    for page in pages:
        reason = classify_page(page)
        if reason is None:
            kept.append(page)
        else:
            skipped[page.page_number] = reason
    return kept, skipped
