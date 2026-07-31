"""CoVe's deterministic numeric comparison. Never an LLM call —
`18.2 == 18.2` is a code-level equality check, never a model judgment.
Delegating this to a model would reintroduce exactly the unreliability the
system exists to remove.

Model-independent: this comparator has no dependency on which model (if
any) performs claim extraction or independent execution — it only compares
the structured records those steps produce, using the same
`normalize_value` every other numeric guard in the system uses
(`contracts/numeric.py`), so this and the FFF rewriter's/proposition
segmenter's guards agree by construction.

A claim matches if its asserted value, unit, period label, and scale all
agree with the independently-executed verification's — value compared
numerically (`normalize_value`, Decimal, never `float`). All four must
agree: a right number in the wrong period or wrong scale is not a match,
it's the exact class of silent unit error this comparator exists to catch.

Period/unit normalization exists because claim extraction (reading the
draft's own prose) and independent verification (restating "exactly as the
evidence states it") are two independent LLM calls with no shared
vocabulary constraint. In practice this produces real false-positive
conflicts: a claim rendering its period as "FY2018" while independent
verification renders the same fiscal year as "year ended December 31,
2018" — genuinely the same period, different words, exact string equality
reads it as a mismatch. The same class of issue shows up as a unit/scale
field-swap: one side writes `unit="USD", scale="millions"`, the other
`unit="millions", scale="millions"` (the model repeats the scale word into
the unit field and never restates the currency). Left unhandled, both
flood the negative-rejection rate with formatting artifacts rather than
real catches, corrupting the C1-vs-C2 comparison this project depends on.

The fix stays inside deterministic code, the same category `normalize_value`
already occupies for raw values — it doesn't touch `numeric_match`'s own
equality logic (still exact comparison, now over canonicalized inputs),
and it doesn't weaken detection of a genuinely different period: `FY2022`
and `FY2023` parse to different `(fiscal_year, quarter)` tuples and still
conflict every time. Normalizing phrasing must never collapse two
different fiscal years together.

Two more instances of the same root cause: (1) independent verification
sometimes embeds the scale word directly into `answer_value`
(`"$1,848 million"` instead of `"1,848"`) — `normalize_value` doesn't fold
scale words by design and doesn't strip them either, so the value silently
fails to parse and the claim reads as unsupported-by-value rather than
compared at all. `strip_scale_word` (`contracts/numeric.py`) is applied to
both value strings before `normalize_value` here too. (2) The two calls
also render the same real currency in different words ("$" vs "dollars"
vs "USD") — a small fixed synonym table canonicalizes these before the
unit comparison, same principle as the unit/scale field-swap fix above.
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
    return claim_period == verification_period  # unparseable on either side -- fall back, never guess


def _scale_word_or_none(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    key = text.strip().lower().rstrip("s")
    return key if key in _SCALE_WORDS else None


def _decompose_compound_scale_currency(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """A single field sometimes encodes both scale and currency in one
    phrase (`answer_unit="millions of dollars"`, a more common phrasing
    than the simple field-swap the scale/unit fix above targets). Splits it
    into `(scale_word, currency_token)`, or `(None, None)` if the text
    isn't this compound shape. Without this, a verification response's
    "millions of dollars" is neither a recognized scale word
    (`_scale_word_or_none` needs an exact match, not a phrase containing
    one) nor a recognized currency synonym — it falls through as "a real,
    different unit" and conflicts with a claim's clean "USD", even though
    both sides agree completely."""
    if not text:
        return None, None
    m = _COMPOUND_SCALE_CURRENCY_RE.search(text)
    if not m:
        return None, None
    return m.group(1).lower(), "USD"


def _canonical_scale(unit: Optional[str], scale: Optional[str]) -> Optional[str]:
    """Prefer the `scale` field's own value; if it isn't actually a scale
    word (the model put it in `unit` instead — the field-confusion this
    fixes), fall back to `unit`. Checks both fields for the compound
    "millions of dollars" shape first, either of which can carry it."""
    for field in (scale, unit):
        compound_scale, _ = _decompose_compound_scale_currency(field)
        if compound_scale is not None:
            return compound_scale
    return _scale_word_or_none(scale) or _scale_word_or_none(unit)


def _canonicalize_currency(token: str) -> str:
    return _CURRENCY_SYNONYMS.get(token, token)


def _canonical_unit(unit: Optional[str], scale: Optional[str]) -> Optional[str]:
    """The real unit/currency token, ignoring whichever field is merely a
    restated scale word rather than an actual unit (e.g. `unit="millions"`
    is not a real unit — treat it as unstated, not as a genuine value that
    could conflict with a real one). Currency synonyms ("$"/"dollars"/"USD")
    are folded to one token before comparison; the compound "millions of
    dollars" shape is decomposed first, either field."""
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
    # A side that never states a real unit (only a restated scale word)
    # doesn't conflict by omission -- absence isn't evidence of disagreement.
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
