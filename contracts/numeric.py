"""Single source of truth for numeric and period normalization.

Several places need identical numeric handling: the proposition segmenter's
verbatim check, the FFF rewriter's numeric guard, CoVe's comparison, and
evaluation scoring against gold answers. If any of them normalized
differently, a value could pass one check and fail the next. Everything
should import from here rather than reimplement normalization.

`parse_fiscal_period` is the same kind of shared authority for periods that
`normalize_value` is for values.

Two distinct operations, kept separate on purpose:

- `normalize_value` — lexical only. Strips display formatting (thousands
  separators, currency symbols, whitespace, a trailing `%`) and applies the
  standard financial-text convention that parentheses mean negative
  (`(1,577)` -> `-1577`), then parses the result as a `Decimal`. It does not
  fold scale words ("million", "bn") into the value — that would make the
  function silently context-dependent — and it doesn't decide whether
  negating a parenthesized figure is semantically correct; that's a decision
  for the caller, not this module.
- `appears_verbatim` — the guard. Tokenizes the numbers out of a source
  text, normalizes them, normalizes the candidate's numbers the same way,
  and checks membership. `18.2` and `18.20` count as the same fact; `18.2`
  and `19.9` don't.

Always `Decimal`, never `float` — floating-point representation error is
exactly the kind of unreliability this module exists to avoid.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

_CURRENCY_SYMBOLS = "$€£¥"
_CURRENCY_SYMBOL_RE = re.compile(f"[{re.escape(_CURRENCY_SYMBOLS)}]")
_SCALE_WORD_RE = re.compile(r"\b(thousand|million|billion)s?\b", re.IGNORECASE)

# Fiscal-period phrasing, ordered most specific first. "quarter ended" /
# "N months ended" phrasings are deliberately not parsed here (see
# _looks_quarterly_without_explicit_quarter) -- guessing which quarter a
# bare date falls in requires knowing the company's fiscal year end, and
# guessing wrong would silently turn a quarterly figure into an annual one.
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

# Numeric tokens as they appear in US-formatted financial text: optional
# accounting parentheses, optional currency symbol, digits with optional
# comma thousands-grouping, optional decimal part, optional trailing
# percent sign. Assumes US formatting (comma = thousands, period = decimal).
NUMERIC_TOKEN_RE = re.compile(
    r"""
    \(\s*[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*%?\s*\)   # accounting-negative: (1,577)
    |
    -?\s*[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*%?         # plain: -1577 / $1,577.0 / 18.2% / 1577
    """,
    re.VERBOSE,
)


def normalize_value(s: Optional[str]) -> Optional[Decimal]:
    """Lexical normalization only. Returns `None` if `s` is `None`, blank, or
    does not parse as a number after stripping display formatting — never
    raises, so guards can treat a bad token as "does not match" rather than
    crashing.
    """
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
    """Strips a scale word ("million", "billion") out of a value string
    before parsing. `normalize_value` doesn't fold scale into the value on
    purpose, so handing it "$1.5 billion" whole would fail to parse -- strip
    the word first so what's left ("$1.5") actually parses."""
    return _SCALE_WORD_RE.sub("", text).strip()


def _looks_quarterly_without_explicit_quarter(text: str) -> bool:
    return bool(_QUARTERLY_HINT_RE.search(text)) and not (_Q_THEN_FY_RE.search(text) or _FY_THEN_Q_RE.search(text))


def parse_fiscal_period(text: Optional[str]) -> Optional[tuple[int, Optional[int]]]:
    """Canonical `(fiscal_year, quarter)` for common period phrasings, or
    `None` if unparseable (or ambiguous, e.g. a bare "quarter ended"/"as of"
    date with no explicit quarter number) — callers fall back to raw string
    comparison, or decline to reject, rather than guessing."""
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
    """True if every numeric token in `candidate` normalizes to a value that
    also appears, normalized, somewhere in `source_text` -- the numeric-
    verbatim guard used by both the segmenter and the FFF rewriter.

    A candidate with no numeric tokens at all vacuously passes: there's
    nothing to verify, and rejecting a purely prose candidate here would be
    a false positive, not a caught hallucination.
    """
    source_values = {
        v for v in (normalize_value(tok) for tok in numeric_tokens(source_text)) if v is not None
    }
    candidate_values = [
        v for v in (normalize_value(tok) for tok in numeric_tokens(candidate)) if v is not None
    ]
    if not candidate_values:
        return True
    return all(v in source_values for v in candidate_values)
