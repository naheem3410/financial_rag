"""Numeric / unit / currency / period extractor — Path A.

Most safety-critical offline component. Tier 1 (XBRL) lives entirely in
Path B. Tier 2 is regex over parsed text, no model, handles most narrative
figures. Tier 3 is a bounded LLM call, only for unresolved qualifiers like
"the prior period" — it classifies, never re-emits the digit, and gets
dropped (not repaired) on schema-invalid output.
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
# "year ended December 31, 2018" style — what the segmenter's own prompt
# actually produces, not "FY2018" shorthand.
_PERIOD_ENDED_RE = re.compile(
    r"\b(?P<period_word>year|quarter)\s+ended\s+(?P<month>[A-Za-z]+)\s+\d{1,2},\s*(?P<year>\d{4})\b",
    re.IGNORECASE,
)
# balance-sheet snapshot phrasing ("at <date>" / "as of <date>") -- a
# balance sheet reports a point in time, never a period "ended", so this
# needs its own pattern rather than routing to the LLM tier as ambiguous.
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

# trailing word after a numeric token, telling it apart from period
# numbers like "2018"/"Q3". Split into unit vs scale dicts since a token
# only carries one of each.
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
        # calendar quarter from the filing's own stated month, so no
        # company-specific fiscal-year-end convention is needed.
        quarter = _MONTH_TO_CALENDAR_QUARTER.get(match.group("month").lower())
        return Period(fiscal_year=year, quarter=quarter)
    match = _AS_OF_RE.search(text)
    if match:
        # same month-to-quarter trick; harmless even for a 10-K's annual
        # snapshot since a query without an explicit quarter ignores it.
        year = int(match.group("year"))
        quarter = _MONTH_TO_CALENDAR_QUARTER.get(match.group("month").lower())
        return Period(fiscal_year=year, quarter=quarter)
    return None


def _trailing_word(text: str, end_offset: int) -> Optional[str]:
    rest = text[end_offset : end_offset + 15]
    match = re.match(r"\s*([A-Za-z]+)", rest)
    return match.group(1).lower() if match else None


def _extract_value_and_units(text: str) -> Optional[tuple[str, str, str, Optional[str]]]:
    """Picks the token that's the value, not part of a period, by preferring
    one followed by a unit/scale word. Falls back to the last numeric token,
    since templated inputs put "<value> <unit>" at the sentence end."""
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
    """Tier 2 — regex, no model. Returns None rather than guessing if the
    period can't be resolved, so the dispatcher falls through to tier 3."""
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
    """Tier 3 — bounded LLM qualifier resolution. Value is still taken from
    text verbatim by code; the model only classifies the qualifier."""
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
    """Tier dispatcher: tier 2 first at zero LLM cost, tier 3 only if it
    couldn't resolve the period and a qualifier was flagged upstream."""
    record = extract_tier2(text)
    if record is not None:
        return ExtractionResult(numeric=record, extraction_tier="rules")

    if ambiguous_qualifier is None:
        return ExtractionResult(numeric=None, extraction_tier=None)

    record = extract_tier3(text, ambiguous_qualifier, document_context, llm_client)
    if record is not None:
        return ExtractionResult(numeric=record, extraction_tier="llm_qualifier")
    return ExtractionResult(numeric=None, extraction_tier=None)
