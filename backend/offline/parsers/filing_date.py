"""Filing period-end date extraction — Path A. No model involved.

Reads the form's own "For the fiscal year/quarterly period ended <date>"
boilerplate directly, rather than assuming a fixed year-end — Adobe's
actual fiscal year ends near Nov 30, not Dec 31, so that assumption breaks.

This is the statement's period-end date, not the later EDGAR filing
timestamp — nothing here supplies or fabricates the latter.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional, Sequence

from offline.parsers.layout_parser import ParsedPage

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_PERIOD_END_RE = re.compile(
    r"For the (?:fiscal year|quarterly period|transition period)\s+ended\s+"
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})",
    re.IGNORECASE,
)


def extract_filing_period_end(pages: Sequence[ParsedPage]) -> Optional[str]:
    """Searches pages for the form's own period-end statement. Returns ISO
    YYYY-MM-DD, or None if no page states it in this exact form."""
    for page in pages:
        match = _PERIOD_END_RE.search(page.text)
        if match:
            month = _MONTH_NAMES.get(match.group("month").lower())
            if month is None:
                continue
            return date(int(match.group("year")), month, int(match.group("day"))).isoformat()
    return None
