"""Generator. LLM structured output, answers from paired FFFs only. For
NPCE-on cells the placeholder substitution already removes any collision
surface; C1 has no split, so the numeric-verbatim guard below is what
keeps it honest instead.

answers is a list (not one value) so C1's unsplit multi-metric questions
can still report a complete answer without a schema artifact confounding
the NPCE comparison.

Every answer_value must appear verbatim in a supplied fact, checked per
item so one bad figure doesn't sink the rest. answerable=true surviving
with nothing valid triggers a bounded retry, then falls back to false --
never guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from contracts.models import FFF
from contracts.numeric import appears_verbatim
from llm.client import LLMClient

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You answer a single financial question using only the facts provided.

Every fact you have concerns exactly one entity, one metric, and one period. The
question concerns the same one. There is nothing to disambiguate and nothing to
combine across entities.

RULES
- Use only the supplied facts. Do not use prior knowledge about this company or
  any other.
- Reproduce values exactly as they appear in the facts.
- Do not compute, derive, convert, or annualise anything. If the question requires
  arithmetic the facts do not already contain, set `answerable` to false.
- If the facts do not contain the answer, set `answerable` to false and leave
  `answers` empty. This is correct behaviour, not failure.
- The question may ask for more than one metric ("report both figures separately") --
  emit one entry in `answers` per metric actually asked, each citing the fff_id(s) it
  came from in `used_fff_ids`.
- Cite the fff_id of every fact you used.
"""


@dataclass(frozen=True)
class AnswerValue:
    metric: str
    value: str
    unit: str


@dataclass(frozen=True)
class GeneratedAnswer:
    answerable: bool
    answers: list[AnswerValue] = field(default_factory=list)
    answer_text: str = ""
    used_fff_ids: list[str] = field(default_factory=list)


def _generate_once(question: str, facts: Sequence[FFF], llm_client: LLMClient) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "question": question,
        "facts": [
            {
                "fff_id": f.fff_id,
                "subject": f.subject,
                "metric": f.metric,
                "period_label": f.period_label,
                "value_text": f.value_text,
            }
            for f in facts
        ],
    }
    return llm_client.complete(payload)


def generate_answer(
    question: str,
    facts: Sequence[FFF],
    llm_client: LLMClient,
    max_attempts: int = 2,
) -> GeneratedAnswer:
    """Never raises on a guard failure -- bounded retry, then falls back
    to answerable=False rather than invent an answer."""
    if not facts:
        return GeneratedAnswer(answerable=False, answer_text="No supporting facts were retrieved for this question.")

    fff_ids = {f.fff_id for f in facts}
    all_value_texts = [f.value_text for f in facts]

    for _ in range(max_attempts):
        response = _generate_once(question, facts, llm_client)
        if not response.get("answerable", False):
            return GeneratedAnswer(
                answerable=False,
                answer_text=response.get("answer_text", "The provided facts do not answer this question."),
            )

        valid_answers = [
            AnswerValue(metric=a.get("metric", ""), value=a.get("answer_value", ""), unit=a.get("answer_unit", ""))
            for a in response.get("answers", [])
            if any(appears_verbatim(a.get("answer_value", ""), source_text) for source_text in all_value_texts)
        ]
        valid_used_fff_ids = [fid for fid in response.get("used_fff_ids", []) if fid in fff_ids]

        if valid_answers and valid_used_fff_ids:
            return GeneratedAnswer(
                answerable=True,
                answers=valid_answers,
                answer_text=response.get("answer_text", ""),
                used_fff_ids=valid_used_fff_ids,
            )
        # answerable=true but nothing survived the guard -- retry rather
        # than trust an ungrounded answer

    return GeneratedAnswer(answerable=False, answer_text="The provided facts do not answer this question.")
