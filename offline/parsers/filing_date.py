"""Filing period-end date extraction — Path A.

No model involved. Every SEC 10-K/10-Q cover page states its own reporting
period in a fixed, boilerplate phrase — "For the fiscal year ended December
31, 2018" or "For the quarterly period ended June 30, 2023" — required by
the form itself, so this is read directly rather than derived from a
naming-convention assumption (e.g. "10-K filings end December 31"). That
assumption doesn't hold in general: Adobe's real cover page states "For the
fiscal year ended December 2, 2016" — its fiscal year ends on a Friday near
November 30, not December 31.

This is the statement's period-end date, not the SEC EDGAR filing
timestamp (typically a few weeks later) — no source in this pipeline
supplies the latter, and this module never fabricates one. Label it
accordingly wherever it's surfaced.
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
    """Searches `pages` (the cover page first, by convention page 0) for the
    form's own "For the [fiscal year|quarterly period] ended <date>"
    statement. Returns an ISO `YYYY-MM-DD` string, or `None` if no page
    states it in this exact boilerplate form — never guessed from the
    document name.
    """
    for page in pages:
        match = _PERIOD_END_RE.search(page.text)
        if match:
            month = _MONTH_NAMES.get(match.group("month").lower())
            if month is None:
                continue
            return date(int(match.group("year")), month, int(match.group("day"))).isoformat()
    return None
