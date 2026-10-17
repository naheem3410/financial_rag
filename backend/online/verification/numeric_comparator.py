"""CoVe's deterministic numeric comparison. Never an LLM call --
`18.2 == 18.2` is code-level equality, not a model judgment.

No dependency on which model (if any) did extraction or verification --
just compares the structured records they produced, via the same
`normalize_value` every other numeric guard in the system uses.

A claim matches only if value, unit, period, and scale all agree (value via
Decimal, never float). A right number in the wrong period/scale is exactly
the silent unit error this exists to catch, not a pass.

Period/unit normalization exists because extraction and verification are
independent LLM calls with no shared vocabulary -- "FY2018" vs "year ended
Dec 31, 2018" is the same period rendered differently, and a naive string
compare would flag it as a false conflict. Same story for unit/scale field
swaps (`unit="millions"` instead of restating currency). Left unhandled
these flooded rejections with formatting noise, not real catches.

The fix stays in deterministic code and never weakens real mismatches --
FY2022 vs FY2023 still conflicts every time. Also strips embedded scale
words from values (`"$1,848 million"` -> `"1,848"`) and folds currency
synonyms ($/dollars/USD) before comparing, same root cause both times.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from contracts.numeric import normalize_value, parse_fiscal_period, strip_scale_word

_SCALE_WORDS = {"unit", "thousand", "million", "billion"}
_CURRENCY_SYNONYMS = {"$": "USD", "USD": "USD", "DOLLAR": "USD", "DOLLARS": "USD", "US DOLLARS": "USD"}
_COMPOUND_SCALE_CURRENCY_RE = re.compile(r"\b(thousand|million|billion)s?\s*(?:of\s*)?(?:dollars?|usd|\$)\b", re.IGNORECASE)


def _periods_match(claim_period: Optional[str], verification_period: Optional[str]) -> bool:
    claim_parsed = parse_fiscal_period(claim_period)
    verification_parsed = parse_fiscal_period(verification_period)
    if claim_parsed is not None and verification_parsed is not None:
        return claim_parsed == verification_parsed
    return claim_period == verification_period  # unparseable side -- fall back, don't guess


def _scale_word_or_none(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    key = text.strip().lower().rstrip("s")
    return key if key in _SCALE_WORDS else None


def _decompose_compound_scale_currency(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Splits a compound phrase like "millions of dollars" into
    `(scale_word, currency_token)`, or `(None, None)` if it's not that
    shape -- otherwise it reads as a "different unit" and false-conflicts."""
    if not text:
        return None, None
    m = _COMPOUND_SCALE_CURRENCY_RE.search(text)
    if not m:
        return None, None
    return m.group(1).lower(), "USD"


def _canonical_scale(unit: Optional[str], scale: Optional[str]) -> Optional[str]:
    """Prefers `scale`; falls back to `unit` if the model put the scale
    word there instead. Checks the compound "millions of dollars" shape first."""
    for field in (scale, unit):
        compound_scale, _ = _decompose_compound_scale_currency(field)
        if compound_scale is not None:
            return compound_scale
    return _scale_word_or_none(scale) or _scale_word_or_none(unit)


def _canonicalize_currency(token: str) -> str:
    return _CURRENCY_SYNONYMS.get(token, token)


def _canonical_unit(unit: Optional[str], scale: Optional[str]) -> Optional[str]:
    """The real unit/currency token, ignoring a field that's just a restated
    scale word (`unit="millions"` isn't a real unit, treat as unstated).
    Currency synonyms fold to one token; compound phrasing decomposed first."""
    for field in (unit, scale):
        _, compound_currency = _decompose_compound_scale_currency(field)
        if compound_currency is not None:
            return compound_currency
    if unit and _scale_word_or_none(unit) is None:
        return _canonicalize_currency(unit.strip().upper())
    if scale and _scale_word_or_none(scale) is None:
        return _canonicalize_currency(scale.strip().upper())
    return None


def _units_match(claim_unit: Optional[str], claim_scale: Optional[str], verification_unit: Optional[str], verification_scale: Optional[str]) -> bool:
    claim_canonical = _canonical_unit(claim_unit, claim_scale)
    verification_canonical = _canonical_unit(verification_unit, verification_scale)
    # absence isn't evidence of disagreement
    if claim_canonical is None or verification_canonical is None:
        return True
    return claim_canonical == verification_canonical


def _normalize_asserted_value(raw: Any) -> Optional[Any]:
    if not isinstance(raw, str):
        return normalize_value(raw)
    return normalize_value(strip_scale_word(raw))


def numeric_match(claim: dict[str, Any], verification: dict[str, Any]) -> bool:
    claim_value = _normalize_asserted_value(claim.get("asserted_value"))
    verification_value = _normalize_asserted_value(verification.get("answer_value"))
    claim_unit, claim_scale = claim.get("asserted_unit"), claim.get("scale")
    verification_unit, verification_scale = verification.get("answer_unit"), verification.get("scale")
    return (
        claim_value is not None
        and claim_value == verification_value
        and _units_match(claim_unit, claim_scale, verification_unit, verification_scale)
        and _periods_match(claim.get("period_label"), verification.get("period_label"))
        and _canonical_scale(claim_unit, claim_scale) == _canonical_scale(verification_unit, verification_scale)
    )
