"""Numeric / unit / currency / period extractor — Path A.

The most safety-critical offline component. Three tiers, in priority order:

1. XBRL tagged facts — handled entirely by Path B
   (offline/parsers/schema_mapper.py `read_xbrl_facts` +
   offline/propositions/row_templating.py); Path A prose carries no XBRL
   tags, so this module only ever reaches tiers 2-3.
2. Rules/regex over parsed text (`extract_tier2`, no model) — handles the
   large majority of narrative figures: an explicit period ("Q3 FY2024",
   "fiscal year 2018") and a value+unit pair found directly in the sentence.
3. Bounded LLM (`extract_tier3`) — invoked only when tier 2 can't resolve
   the period ("the prior period", "the quarter"). The model classifies
   what the qualifier refers to; it never re-emits the numeric value, which
   tier 3 still takes verbatim from the source text by code. Output is
   validated against `llm.schemas.NumericQualifierResolution` — schema-
   invalid output (including a digit leaking into `reasoning`) is dropped,
   never repaired.

`extract` is the tier dispatcher: try tier 2; fall back to tier 3 only if
tier 2 couldn't resolve the period and an `ambiguous_qualifier` phrase was
identified upstream. It reports which tier produced the result so a caller
can set `Confidence.extraction_tier` correctly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from contracts.models import NumericRecord, Period
from contracts.numeric import NUMERIC_TOKEN_RE, normalize_value
from llm.client import LLMClient
from llm.schemas import NumericQualifierResolution

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You resolve ambiguous temporal and unit qualifiers in financial statements.

You are given a proposition and its document context. Your only task is to
CLASSIFY what an ambiguous qualifier refers to — for example, which fiscal year
"the quarter" or "the prior period" denotes, or whether figures are in thousands,
millions, or billions.

ABSOLUTE RULE
You never output a numeric value. You never restate, round, or recompute a figure.
You return only classifications drawn from the allowed enumerations below. If the
context does not determine the answer, return null. Guessing is a failure; null
is a correct answer.
"""

_QUARTER_FY_RE = re.compile(r"\bQ(?P<quarter>[1-4])\s*FY\s*(?P<year>\d{4})\b", re.IGNORECASE)
_FY_ONLY_RE = re.compile(r"\bFY\s*(?P<year>\d{4})\b", re.IGNORECASE)
_FISCAL_YEAR_WORDS_RE = re.compile(r"\bfiscal year\s+(?P<year>\d{4})\b", re.IGNORECASE)
# "in the year ended December 31, 2018" / "for the quarter ended June 30,
# 2023" — the phrasing the proposition segmenter's own system prompt
# actually produces (it restates statement headers like "Years ended
# December 31" plus the specific column year), rather than "FY2018"
# shorthand.
_PERIOD_ENDED_RE = re.compile(
    r"\b(?P<period_word>year|quarter)\s+ended\s+(?P<month>[A-Za-z]+)\s+\d{1,2},\s*(?P<year>\d{4})\b",
    re.IGNORECASE,
)
# "3M Company had accounts payable of $2,266 million at December 31, 2018" /
# "...as of June 30, 2023" -- balance-sheet snapshot phrasing.
# `_PERIOD_ENDED_RE` only recognizes "year/quarter ended", which flow-
# statement and income-statement propositions use, but a balance sheet
# reports a point-in-time snapshot ("at <date>"), never a period "ended".
# This isn't genuine ambiguity for the LLM tier to resolve -- tier 2 just
# needs its own pattern for this phrasing.
_AS_OF_RE = re.compile(
    r"\b(?:at|as\s+of)\s+(?P<month>[A-Za-z]+)\s+\d{1,2},\s*(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_MONTH_TO_CALENDAR_QUARTER = {
    "january": 1, "february": 1, "march": 1,
    "april": 2, "may": 2, "june": 2,
    "july": 3, "august": 3, "september": 3,
    "october": 4, "november": 4, "december": 4,
}

# Words immediately after a numeric token that identify it as *the* value,
# distinguishing it from period numbers ("2018", "Q3") that also look
# numeric. Unit words describe NumericRecord.unit; scale words describe
# NumericRecord.scale -- kept in separate dicts since a token can only
# carry one of each and they mean different things.
_UNIT_WORDS = {"percent": "percent", "ratio": "ratio", "shares": "count", "count": "count"}
_SCALE_WORDS = {
    "thousand": "thousands",
    "thousands": "thousands",
    "million": "millions",
    "millions": "millions",
    "billion": "billions",
    "billions": "billions",
}


@dataclass(frozen=True)
class ExtractionResult:
    numeric: Optional[NumericRecord]
    extraction_tier: Optional[str]  # "rules" | "llm_qualifier" | None


def _extract_period_tier2(text: str) -> Optional[Period]:
    match = _QUARTER_FY_RE.search(text)
    if match:
        return Period(fiscal_year=int(match.group("year")), quarter=int(match.group("quarter")))
    match = _FY_ONLY_RE.search(text) or _FISCAL_YEAR_WORDS_RE.search(text)
    if match:
        return Period(fiscal_year=int(match.group("year")))
    match = _PERIOD_ENDED_RE.search(text)
    if match:
        year = int(match.group("year"))
        if match.group("period_word").lower() == "year":
            return Period(fiscal_year=year)
        # "quarter ended <month> ..." -- the calendar quarter the stated
        # month falls in. This reads the month directly from the filing's
        # own stated date, so it needs no company-specific fiscal-year-end
        # convention (unlike deriving a quarter from a company's fiscal
        # calendar in the abstract).
        quarter = _MONTH_TO_CALENDAR_QUARTER.get(match.group("month").lower())
        return Period(fiscal_year=year, quarter=quarter)
    match = _AS_OF_RE.search(text)
    if match:
        # Balance-sheet snapshot date -- same calendar-quarter-from-month
        # convention as the "quarter ended" branch above. Setting `quarter`
        # even for a 10-K's annual year-end snapshot is harmless: a
        # `SubQuery.period` with no explicit quarter never filters on it
        # (online/retrieval/metadata_filter.py), so this only ever narrows
        # a query that actually asked for that specific quarter.
        year = int(match.group("year"))
        quarter = _MONTH_TO_CALENDAR_QUARTER.get(match.group("month").lower())
        return Period(fiscal_year=year, quarter=quarter)
    return None


def _trailing_word(text: str, end_offset: int) -> Optional[str]:
    rest = text[end_offset : end_offset + 15]
    match = re.match(r"\s*([A-Za-z]+)", rest)
    return match.group(1).lower() if match else None


def _extract_value_and_units(text: str) -> Optional[tuple[str, str, str, Optional[str]]]:
    """Picks the numeric token that is *the value* rather than part of a
    period expression, by preferring a token immediately followed by a known
    unit/scale word (e.g. "18.2 percent", "$32.2 billion"). Falls back to the
    last numeric token in the sentence if none has such a neighbor — this
    module's templated inputs (row_templating.py, the segmenter's system
    prompt) consistently place "<value> <unit>" at the end of the sentence.
    """
    matches = list(NUMERIC_TOKEN_RE.finditer(text))
    if not matches:
        return None

    chosen, chosen_word = None, None
    for m in matches:
        word = _trailing_word(text, m.end())
        if word in _UNIT_WORDS or word in _SCALE_WORDS:
            chosen, chosen_word = m, word
            break
    if chosen is None:
        chosen = matches[-1]
        chosen_word = _trailing_word(text, chosen.end())

    value_token = chosen.group(0).strip()
    unit = _UNIT_WORDS.get(chosen_word, "absolute")
    scale = _SCALE_WORDS.get(chosen_word, "unit")
    currency = "USD" if any(sym in value_token for sym in "$€£¥") else None

    return value_token, unit, scale, currency


def extract_tier2(text: str) -> Optional[NumericRecord]:
    """Tier 2 — regex over parsed text, no model. Returns `None` (rather
    than guessing) if the period can't be resolved from the text alone, so
    the dispatcher can fall through to tier 3."""
    period = _extract_period_tier2(text)
    if period is None:
        return None

    found = _extract_value_and_units(text)
    if found is None:
        return None
    value_token, unit, scale, currency = found

    value = normalize_value(value_token)
    if value is None:
        return None

    return NumericRecord(value=float(value), scale=scale, currency=currency, unit=unit, period=period)


def extract_tier3(
    text: str,
    ambiguous_qualifier: str,
    document_context: dict[str, Any],
    llm_client: LLMClient,
) -> Optional[NumericRecord]:
    """Tier 3 — bounded LLM qualifier resolution. The value is still taken
    from `text` verbatim by code; the model only classifies what the
    qualifier refers to.
    """
    found = _extract_value_and_units(text)
    if found is None:
        return None
    value_token, unit, scale_hint, currency = found

    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "proposition_text": text,
        "ambiguous_qualifier": ambiguous_qualifier,
        "document_context": document_context,
    }
    raw_response = llm_client.complete(payload)

    try:
        resolution = NumericQualifierResolution.model_validate(raw_response)
    except Exception:
        return None  # schema-invalid -> drop, never repair

    if not resolution.resolvable or resolution.fiscal_year is None:
        return None  # "not resolvable" is a correct answer, not an error

    value = normalize_value(value_token)
    if value is None:
        return None

    return NumericRecord(
        value=float(value),
        scale=resolution.scale or scale_hint,
        currency=resolution.currency or currency,
        unit=unit,
        period=Period(
            fiscal_year=resolution.fiscal_year,
            quarter=resolution.quarter,
            basis=resolution.basis or "reported",
        ),
    )


def extract(
    text: str,
    document_context: dict[str, Any],
    llm_client: LLMClient,
    ambiguous_qualifier: Optional[str] = None,
) -> ExtractionResult:
    """Tier dispatcher: tier 2 first, at zero LLM cost; tier 3 only if tier 2
    couldn't resolve the period and an `ambiguous_qualifier` was identified
    upstream. Reports which tier produced the result so a caller can set
    `Confidence.extraction_tier` correctly.
    """
    record = extract_tier2(text)
    if record is not None:
        return ExtractionResult(numeric=record, extraction_tier="rules")

    if ambiguous_qualifier is None:
        return ExtractionResult(numeric=None, extraction_tier=None)

    record = extract_tier3(text, ambiguous_qualifier, document_context, llm_client)
    if record is not None:
        return ExtractionResult(numeric=record, extraction_tier="llm_qualifier")
    return ExtractionResult(numeric=None, extraction_tier=None)
