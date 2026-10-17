"""The router: one front door that decides how to handle a question,
before any retrieval happens. Step 4.1, decision 11.

Money or paper is never guessed from the words -- that choice is the
document shelf's, set at upload, not the router's (decision 11). This
module only ever sees money (financial) questions for now.

Road order follows decision 11's own diagram exactly: a split signal
("and"/"both"/"each"/"compare"/"vs") wins first, then a step-dependency
signal (step 4.6 -- "one piece needs another's answer"), then the plain
"straight" default. Both checks are rules, never a model that guesses --
the model in step 4.6 only plans the steps once this road is chosen, it
never decides whether to take it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence, TypeVar

Road = Literal["straight", "split_money", "step_by_step"]

MAX_PIECES = 6

# "and", "both", "each", "compare", "versus"/"vs" -- decision 11's own list
# of split signals, checked as whole words so "sandwiches" or "eaches"
# never trip a false split.
_SPLIT_SIGNAL_RE = re.compile(r"\b(and|both|each|compare|versus|vs\.?)\b", re.IGNORECASE)

# Step 4.6: a superlative/ordinal phrase pointing at an entity that isn't
# named yet ("the company with the highest revenue", "whichever segment
# had the largest loss") -- the question can't be answered in one search
# because finding *which* company/segment/year is itself a sub-question.
# A curated list, not exhaustive, the same honest trade-off the split
# signal list above already makes.
_STEP_SIGNAL_RE = re.compile(
    r"\b(the (company|companies|segment|division|subsidiary|competitor|one|year|quarter) (with|that|which|who)"
    r"|whichever (company|segment|division|subsidiary|year|quarter))\b"
    r".{0,40}\b(highest|largest|lowest|smallest|biggest|best|worst|most|least)\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class RouteDecision:
    road: Road
    reason: str
    signal: str | None  # the exact word/phrase that triggered the road, for the log
    question: str  # always the ORIGINAL words -- nothing here ever rewrites them


def choose_road(question: str) -> RouteDecision:
    """Counts and simple rules, never a model that guesses (decision 11).
    No split or step-dependency signal found is itself the "straight"
    rule, not a fallback guess -- the plain, safe default for an ordinary
    one-fact question."""
    split_match = _SPLIT_SIGNAL_RE.search(question)
    if split_match:
        return RouteDecision(
            road="split_money", reason=f'the question contains "{split_match.group(0).lower()}"',
            signal=split_match.group(0).lower(), question=question,
        )
    step_match = _STEP_SIGNAL_RE.search(question)
    if step_match:
        return RouteDecision(
            road="step_by_step",
            reason=f'the question needs one piece\'s answer before it can ask the next ("{step_match.group(0).strip()}")',
            signal=step_match.group(0).strip(), question=question,
        )
    return RouteDecision(road="straight", reason="no split or step-dependency signal found", signal=None, question=question)


T = TypeVar("T")


def cap_pieces(pieces: Sequence[T], max_pieces: int = MAX_PIECES) -> tuple[list[T], bool]:
    """Decision 11's guard rail: at most `max_pieces` sub-queries, whatever
    the split produced. Returns (possibly-truncated list, whether it had
    to truncate) so a caller can say so plainly rather than silently drop
    the extra pieces without a word."""
    if len(pieces) <= max_pieces:
        return list(pieces), False
    return list(pieces[:max_pieces]), True


def describe_routing(decision: RouteDecision, piece_count: int, capped: bool) -> dict:
    """The plain-words record for "How it happened" (step 4.7 surfaces
    this through the API and the screen; this is the data it will use).
    Every road writes to this -- decision 11's own requirement."""
    if decision.road == "straight":
        summary = "Answered straight: one plain question, no split needed."
    elif decision.road == "step_by_step":
        summary = f"Answered step by step because {decision.reason}."
    else:
        summary = f"Split into {piece_count} piece{'s' if piece_count != 1 else ''} because {decision.reason}."
        if capped:
            summary += f" Capped at {MAX_PIECES} pieces; the rest were left out."
    return {
        "road": decision.road,
        "reason": decision.reason,
        "question": decision.question,
        "piece_count": piece_count,
        "capped": capped,
        "summary": summary,
    }
