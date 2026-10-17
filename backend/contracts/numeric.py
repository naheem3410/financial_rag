"""Single source of truth for numeric/period normalization — segmenter, FFF guard, CoVe, and eval scoring all import from here.
normalize_value is lexical-only (Decimal, never float); appears_verbatim is the verbatim-membership guard built on top of it."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

_CURRENCY_SYMBOLS = "$€£¥"
_CURRENCY_SYMBOL_RE = re.compile(f"[{re.escape(_CURRENCY_SYMBOLS)}]")
_SCALE_WORD_RE = re.compile(r"\b(thousand|million|billion)s?\b", re.IGNORECASE)

# Ordered most specific first. "quarter ended"/"N months ended" isn't parsed here —
# guessing the quarter without knowing fiscal year end risks silently mislabeling the period.
_Q_THEN_FY_RE = re.compile(r"\bQ([1-4])\D{0,10}FY\s*(\d{4})\b", re.IGNORECASE)
_FY_THEN_Q_RE = re.compile(r"\bFY\s*(\d{4})\D{0,10}Q([1-4])\b", re.IGNORECASE)
_FY_RE = re.compile(r"\bFY\s*(\d{4})\b", re.IGNORECASE)
_FISCAL_YEAR_RE = re.compile(r"\bfiscal\s+year\s+(\d{4})\b", re.IGNORECASE)
_YEAR_ENDED_RE = re.compile(r"\byear\s+ended\b.*?(\d{4})\b", re.IGNORECASE)
# "as of <date>" is the dominant phrasing for balance-sheet propositions
# ("accounts payable of $X million as of December 31, 2022").
_AS_OF_RE = re.compile(r"\bas\s+of\b.*?(\d{4})\b", re.IGNORECASE)
_BARE_YEAR_RE = re.compile(r"\b(\d{4})\b")
_QUARTERLY_HINT_RE = re.compile(r"\b(quarter|three|six|nine)\s+months?\s+ended\b|\bquarter\s+ended\b", re.IGNORECASE)

# Numeric tokens as they appear in US-formatted financial text (comma=thousands, period=decimal).
# Handles accounting parens, currency symbols, and trailing percent signs.
NUMERIC_TOKEN_RE = re.compile(
    r"""
    \(\s*[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*%?\s*\)   # accounting-negative: (1,577)
    |
    -?\s*[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*%?         # plain: -1577 / $1,577.0 / 18.2% / 1577
    """,
    re.VERBOSE,
)


def normalize_value(s: Optional[str]) -> Optional[Decimal]:
    """Lexical normalization only; returns None rather than raising on anything that doesn't parse.
    Lets guards treat a bad token as "no match" instead of crashing."""
    if s is None:
        return None
    text = s.strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    text = _CURRENCY_SYMBOL_RE.sub("", text).strip()

    if text.endswith("%"):
        text = text[:-1].strip()

    text = text.replace(",", "")

    if not text:
        return None

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None

    return -value if negative else value


def strip_scale_word(text: str) -> str:
    """Strips a scale word ("million", "billion") before parsing.
    normalize_value deliberately doesn't fold scale in, so "$1.5 billion" needs this first or it won't parse."""
    return _SCALE_WORD_RE.sub("", text).strip()


def _looks_quarterly_without_explicit_quarter(text: str) -> bool:
    return bool(_QUARTERLY_HINT_RE.search(text)) and not (_Q_THEN_FY_RE.search(text) or _FY_THEN_Q_RE.search(text))


def parse_fiscal_period(text: Optional[str]) -> Optional[tuple[int, Optional[int]]]:
    """Canonical (fiscal_year, quarter) for common phrasings, or None if unparseable/ambiguous.
    Callers fall back to raw string comparison rather than guessing."""
    if not text:
        return None
    if _looks_quarterly_without_explicit_quarter(text):
        return None

    m = _Q_THEN_FY_RE.search(text)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = _FY_THEN_Q_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _FY_RE.search(text)
    if m:
        return int(m.group(1)), None
    m = _FISCAL_YEAR_RE.search(text)
    if m:
        return int(m.group(1)), None
    m = _YEAR_ENDED_RE.search(text)
    if m:
        return int(m.group(1)), None
    m = _AS_OF_RE.search(text)
    if m:
        return int(m.group(1)), None
    m = _BARE_YEAR_RE.search(text)
    if m:
        return int(m.group(1)), None
    return None


def numeric_tokens(text: str) -> list[str]:
    """Every numeric-looking substring in `text`, in order of appearance."""
    return [m.group(0).strip() for m in NUMERIC_TOKEN_RE.finditer(text)]


def appears_verbatim(candidate: str, source_text: str) -> bool:
    """True if every numeric token in candidate normalizes to a value present in source_text.
    A candidate with no numbers vacuously passes — nothing to verify, not a caught hallucination."""
    source_values = {
        v for v in (normalize_value(tok) for tok in numeric_tokens(source_text)) if v is not None
    }
    candidate_values = [
        v for v in (normalize_value(tok) for tok in numeric_tokens(candidate)) if v is not None
    ]
    if not candidate_values:
        return True
    return all(v in source_values for v in candidate_values)
