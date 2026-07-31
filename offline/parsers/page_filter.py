"""Content-based page filter — Path A.

Skips pages that never carry citable financial propositions — cover pages,
tables of contents, signature blocks, exhibit indices, SEC certifications —
before they reach the proposition segmenter, cutting both LLM cost and
noise for no faithfulness loss (nothing on these pages is a retrievable
fact).

Content-based only: classification looks solely at each page's own text,
never at which pages happen to carry gold evidence. Filtering on gold
labels would leak evaluation data into the indexing pipeline and invalidate
the retrieval metrics this pipeline exists to produce — the filter must
work identically whether or not a question ever references the document.

Two false-positive classes matter here:

1. Many EDGAR-filed 10-Ks print a running header — literally the words
   "Table of Contents" — on every page as a navigation aid, not only on
   the actual table-of-contents listing. A bare substring match on that
   phrase would misclassify most of a filing's real pages as
   table-of-contents. Fixed by requiring a density of `Item N.` entries (a
   real 10-K TOC lists ~14-16 SEC Items) rather than the phrase alone.
2. The bare word "certification" appears in ordinary legal narrative —
   "class certification" in litigation, product/regulatory certifications
   — with no relation to an SEC officer certification page. Fixed by
   dropping the bare-word pattern and keeping only the two SOX-specific
   boilerplate patterns (Section 302 / 18 U.S.C. §1350, and the
   "I, NAME, certify that:" opening line), which are page-specific rather
   than word-specific.
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
# Deliberately not a bare "\bCERTIFICATIONS?\b" match -- that also matches
# ordinary legal narrative ("class certification"). Only the SOX-specific
# boilerplate that genuinely appears on an officer-certification page.
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

# A real 10-K table of contents lists ~14-16 SEC Items ("Item 1.", "ITEM 1A"
# -- the trailing period is inconsistent across filers, so it's optional
# here). A narrative page that references an item in passing ("as discussed
# in Item 7") does so once or twice, never this many times. Density, not
# the "Table of Contents" phrase itself (often also just a running page
# header), is what actually distinguishes the listing page.
_ITEM_ENTRY_RE = re.compile(r"\bItem\s+\d+[A-Za-z]?\.?\s", re.IGNORECASE)
_MIN_ITEM_ENTRIES_FOR_TOC = 5


def classify_page(page: ParsedPage) -> Optional[SkipReason]:
    """A skip reason if `page` matches a known non-substantive pattern,
    else `None` — the page should be segmented normally."""
    text = page.text

    if len(_ITEM_ENTRY_RE.findall(text)) >= _MIN_ITEM_ENTRIES_FOR_TOC:
        return "table_of_contents"

    for reason, patterns in _RULES:
        if any(p.search(text) for p in patterns):
            return reason
    return None


def filter_pages(pages: list[ParsedPage]) -> tuple[list[ParsedPage], dict[int, SkipReason]]:
    """Splits `pages` into (kept, skipped). `skipped` maps `page_number` ->
    reason, for reporting (e.g. the pilot runner's "pages skipped by the
    filter" metric)."""
    kept: list[ParsedPage] = []
    skipped: dict[int, SkipReason] = {}
    for page in pages:
        reason = classify_page(page)
        if reason is None:
            kept.append(page)
        else:
            skipped[page.page_number] = reason
    return kept, skipped
