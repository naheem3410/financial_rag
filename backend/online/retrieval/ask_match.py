"""Does a candidate fact match what the sub-query actually asks for?

Plain rules only, no model. Two checks, each scored +1 (matches), 0 (can't
tell) or -1 (clearly the wrong kind of fact):

- kind: is it the asked metric, in the asked shape? A balance metric
  (accounts payable, inventories...) is a value "at" a date; a "change in"
  line or a "for the year ended" line with the same words is a cash-flow
  movement, a different fact. A "value" that is just the year is no value.
- year: does the fact's own fiscal year match the asked one?

Used by the reranker's tie-break/blend and by the pipeline's last-chance
guard, so the same rule decides in every place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from contracts.models import Proposition, SubQuery
from offline.labeling.metric_labeler import BALANCE_SHEET_METRICS, is_period_flow_not_balance

_CHANGE_IN_RE = re.compile(r"\b(?:changes?|increases?|decreases?)\s+in\b", re.IGNORECASE)


@dataclass(frozen=True)
class AskMatch:
    kind: int
    year: int

    @property
    def mismatched(self) -> bool:
        return self.kind < 0 or self.year < 0


def _year_only_value(p: Proposition) -> bool:
    # The extractor sometimes grabs the year itself as the number ("2022.0", unit scale).
    n = p.numeric
    return n is not None and n.scale == "unit" and n.value == float(n.period.fiscal_year)


def kind_signal(sq: SubQuery, p: Proposition) -> int:
    if not sq.metric:
        return 0
    if _year_only_value(p):
        return -1
    lowered = p.text.lower()
    if sq.metric in BALANCE_SHEET_METRICS and (
        is_period_flow_not_balance(sq.metric, lowered) or _CHANGE_IN_RE.search(lowered)
    ):
        return -1
    return 1 if sq.metric in p.metrics else 0


def year_signal(sq: SubQuery, p: Proposition) -> int:
    if p.numeric is None:
        return 0
    return 1 if p.numeric.period.fiscal_year == sq.period.fiscal_year else -1


def ask_match(sq: SubQuery, p: Proposition) -> AskMatch:
    return AskMatch(kind=kind_signal(sq, p), year=year_signal(sq, p))


def drop_clear_mismatches(sq: SubQuery, props: Sequence[Proposition]) -> list[Proposition]:
    """Keeps rank order, drops facts that are clearly the wrong kind or year.
    If that would leave nothing, hands back the list untouched."""
    kept = [p for p in props if not ask_match(sq, p).mismatched]
    return kept if kept else list(props)


def kind_tag(p: Proposition) -> str:
    """A short plain-words tag for the reranker's own input text (never shown to users)."""
    lowered = p.text.lower()
    if _CHANGE_IN_RE.search(lowered):
        return "change during the period"
    if re.search(r"\b(?:at|as of)\s+(?:the\s+end\s+of\s+)?[a-z]+\s+\d", lowered):
        return "balance at period end"
    if re.search(r"\b(?:for|during|in)\s+(?:the\s+)?(?:fiscal\s+)?(?:year|quarter)", lowered):
        return "total for the period"
    return ""
