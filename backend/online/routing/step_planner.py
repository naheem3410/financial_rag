"""Step by step (step 4.6, decision 11, the seventh model job): a planner
that breaks a question needing multiple hops -- "the answer to piece one
feeds into piece two" -- into an ordered list of plain-language steps.

Written reason (the plan's own words): some questions need the answer of
one piece to ask the next, and no other job -- not the query splitter, not
any rule -- can plan that. The project's own written rule of exactly six
call sites is changed, in writing, to seven for this one call site,
`step_planner`.

The model only ever plans; it never runs a step and never answers anything
itself. Every plan it proposes is checked by code, here, before a single
step is executed:
  - step ids must be a plain 1..N sequence, no gaps, no duplicates
  - a step's question may only reference an EARLIER step's answer
    ("{step_1}", "{step_2}", ...) -- a step referencing itself or a later
    step is a loop and the whole plan is rejected, not patched
  - the plan is capped at MAX_STEPS; a longer plan is truncated and this
    is reported, the same "cap and say so" pattern as the split road's
    6-piece cap (step 4.1)
A rejected or capped plan never crashes anything -- the caller decides
what "no usable plan" means for it (the pipelines fall back to answering
the question straight, the same safe default every other fallback in this
project uses when a model's output can't be trusted).

Honest, deliberate simplification: a "{step_N}" reference substitutes that
earlier step's ENTIRE answer sentence, not just an extracted name or value
-- there's no field anywhere in this project that separates "the one fact
this answer is about" from the sentence itself, and guessing one out of
free text would be exactly the kind of invented certainty this project
avoids elsewhere. The planner is told to phrase later steps so a whole
sentence still reads naturally in place (see SYSTEM_PROMPT).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from llm.client import LLMClient
from llm.schemas import StepPlanOutput

SCHEMA_VERSION = "1.0"

# Decision 11 calls for "a small cap on steps" without naming a number.
# Chosen deliberately small: each step is a full retrieval-and-generation
# pass, not a cheap sub-query slot like the 6-piece split cap, so the same
# number would be far more expensive to run and to explain in the log.
MAX_STEPS = 4

SYSTEM_PROMPT = """\
You break a question that needs several steps into an ordered list of
plain, self-contained questions -- one step at a time.

Use this only when answering the question genuinely requires knowing the
answer to an earlier step first (for example: first find which company
had the highest revenue, then ask about that company's profit margin). If
the question can be answered in one step, return a single step, or an
empty list if there is really nothing to plan.

A later step that needs an earlier step's answer MUST refer to it by
writing the exact literal text "{step_1}", "{step_2}", and so on (curly
braces included), using the earlier step's own step_id. Do not paraphrase
this as "the company from step 1" or "the company identified above" --
only the exact "{step_N}" form is understood; anything else is treated as
if the step needs nothing from before it. This placeholder gets replaced
with the FULL SENTENCE the earlier step answered with, not just a name or
a number, so phrase the later step so that sentence still reads naturally
in place. For example, for "What was the net income of the company with
the highest revenue in 2022?":
  step 1: "Which company had the highest revenue in 2022?"
  step 2: "Given that {step_1}, what was its net income in 2022?"
A step may never refer to its own step_id or to a step that comes after
it -- that would be a loop, and is never correct.

ABSOLUTE RULE
You never invent facts inside a step's question -- each step is just a
question to go find the answer to, not an answer itself. Step ids must
count up from 1 with no gaps and no repeats.
"""

_STEP_REFERENCE_RE = re.compile(r"\{step_(\d+)\}")


@dataclass(frozen=True)
class StepPlanStep:
    step_id: int
    question: str


@dataclass(frozen=True)
class StepPlan:
    steps: tuple[StepPlanStep, ...]
    capped: bool


def validate_step_plan(raw_steps: list[dict[str, Any]], max_steps: int = MAX_STEPS) -> Optional[StepPlan]:
    """Code-side check of a raw (already schema-validated) step list, never
    trusting the model's own claim that its plan is sound. Returns `None`
    -- "this plan can't be trusted" -- for an empty plan, a non-sequential
    id list, or any loop (a step referencing itself or a later step).
    A too-long-but-otherwise-sound plan is truncated and marked `capped`,
    the one case that's fixed up rather than rejected outright."""
    if not raw_steps:
        return None

    ids = [s["step_id"] for s in raw_steps]
    if ids != list(range(1, len(ids) + 1)):
        return None  # gap, duplicate, or out-of-order id -- untrustworthy, don't guess at a fix

    for step in raw_steps:
        for referenced in (int(n) for n in _STEP_REFERENCE_RE.findall(step["question"])):
            if referenced >= step["step_id"]:
                return None  # a step referencing itself or a later step is a loop

    capped = len(raw_steps) > max_steps
    kept = raw_steps[:max_steps]
    return StepPlan(steps=tuple(StepPlanStep(step_id=s["step_id"], question=s["question"]) for s in kept), capped=capped)


def plan_steps(question: str, llm_client: LLMClient) -> Optional[StepPlan]:
    """Asks the planner, then runs its answer through `validate_step_plan`.
    Any bad or unreadable response -- like every other fallback in this
    project -- is treated as "no usable plan", never a crash."""
    payload = {
        "system_prompt": SYSTEM_PROMPT,
        "schema_version": SCHEMA_VERSION,
        "question": question,
    }
    try:
        raw_response = llm_client.complete(payload)
        resolution = StepPlanOutput.model_validate(raw_response)
    except Exception:
        return None
    return validate_step_plan([s.model_dump() for s in resolution.steps])


def substitute_step_answers(question: str, answers_by_step_id: dict[int, str]) -> str:
    """Replaces every "{step_N}" reference in a step's question with that
    earlier step's already-resolved answer text. A reference to a step
    that hasn't actually run (shouldn't happen once validate_step_plan has
    passed the plan, but never trusted blindly) is left as-is rather than
    guessed at."""
    def _replace(match: re.Match) -> str:
        step_id = int(match.group(1))
        return answers_by_step_id.get(step_id, match.group(0))

    return _STEP_REFERENCE_RE.sub(_replace, question)
