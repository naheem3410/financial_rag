""""How it happened" (step 4.7): turns everything the router and its roads
(4.1-4.6) already recorded on a C3AnswerResult/C4AnswerResult into one
plain-words section -- the road taken and why, the question as understood,
the pieces, the other names tried on a retry, the several-versions note,
the steps, and whether the final check used the original question. No new
logic here, only assembly: every field this reads was already built and
returned by an earlier step.

The "several near-identical results -> answer each, labelled" road
(decision 11, step 4.1's own test table row, "top results almost the same,
differ in one detail") was found missing from steps 4.1-4.6 while this
step was first built (see KNOWN_ISSUES.md's 2026-09-27 entry), then built
for real once the owner confirmed to build it (online/curation/near_identical.py).
`several_versions_note` now reports it honestly whenever it actually fired.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from online.pipeline_c3 import C3AnswerResult
from online.pipeline_c4 import C4AnswerResult

AnswerResult = Union[C3AnswerResult, C4AnswerResult]


@dataclass(frozen=True)
class HowItHappened:
    road: str
    reason: str
    original_question: str
    question_understood: str
    pieces: tuple[str, ...]
    retry_attempted: bool
    retry_names_tried: tuple[str, ...]
    several_versions_note: Optional[str]  # None unless a near-identical-versions collision actually fired
    steps: tuple[dict[str, Any], ...]
    # True on the straight/split roads: the abstention/verification check
    # is built against the real top-level `question`, never the fixed
    # `working_question`. Honestly False when steps ran (step 4.6): each
    # step's own check is against THAT step's own re-phrased question, not
    # the original composite one -- a real, open gap, not glossed over.
    checked_against_original_question: bool
    summary: str  # the one-sentence plain-words version, for the screen


def _several_versions_note(result: AnswerResult) -> Optional[str]:
    if not result.near_identical_detected:
        return None
    if result.near_identical_detail == "basis":
        return "Found two different values for the same period (an original figure and a restated one) -- both are answered, labelled, instead of picking one."
    return f"Found two versions of the same fact that differ in {result.near_identical_detail} -- both are answered, labelled, instead of picking one."


def _summary(result: AnswerResult) -> str:
    """Steps take priority when any actually ran. Otherwise this describes
    whatever the normal splitter path actually did -- by the real piece
    count, not just the router's road label, since a rejected step plan
    (step 4.6's own safe-default fallback) still reports `routing_road ==
    "step_by_step"` even though no steps ran and the ordinary split-or-not
    path answered it instead."""
    if result.question_fix_outcome == "needs_clarification":
        return "Asked for more detail instead of guessing: a word in the question didn't point to a named company."
    if result.step_plan:
        step_count = len(result.step_plan)
        return f"Answered step by step, in {step_count} step{'s' if step_count != 1 else ''}, because {result.routing_reason}."
    piece_count = len(result.pieces)
    if piece_count > 1:
        summary = f"Split into {piece_count} pieces because {result.routing_reason}."
        if result.routing_capped:
            summary += " Capped at 6 pieces; the rest were left out."
    else:
        summary = "Answered straight: one plain question, no split needed."
    if result.near_identical_detected:
        summary += " Two versions of the fact were found and both are shown, labelled."
    if result.question_fix_outcome == "fixed":
        summary = "Fixed a typo in the question, then " + summary[0].lower() + summary[1:]
    return summary


def describe_how_it_happened(result: AnswerResult) -> HowItHappened:
    """Builds the whole "How it happened" section from a finished answer
    result. Works for both C3 and C4 results -- both carry the same set of
    step-4.1-through-4.6 fields."""
    return HowItHappened(
        road=result.routing_road,
        reason=result.routing_reason,
        original_question=result.question,
        question_understood=result.question_understood,
        pieces=result.pieces,
        retry_attempted=result.retry_attempted,
        retry_names_tried=result.retry_names_tried,
        several_versions_note=_several_versions_note(result),
        steps=result.step_plan,
        checked_against_original_question=not bool(result.step_plan),
        summary=_summary(result),
    )
