"""Generator. LLM structured output. Answers from paired FFFs only —
because the input is atomic and placeholder-substituted (under NPCE),
there's no collision surface to hallucinate across. C1 (NPCE off) has no
placeholder substitution, so that guarantee doesn't hold structurally here
the way it will for C3/C4 — the numeric-verbatim guard below is what keeps
C1 honest instead.

Multi-metric answers: a single-metric shape (bare `answer_value`/
`answer_unit` fields) is correct for the NPCE-on case, where the query
splitter has already split a query down to one metric per sub-query. C1
(NPCE off) has no split: a metric-axis question like "3M's accounts
receivable and total current assets" arrives as one sub-query covering two
metrics. `llm/schemas.py:GeneratorOutput.answers` generalizes to a list —
one item per metric actually asked — so C1 can report a complete answer
instead of being structurally capped at one metric regardless of retrieval
quality, which would confound the C1-vs-C3/C4 comparison with a schema
artifact rather than a real NPCE effect.

Numeric-verbatim guard (mandatory, generalizes the single-value guard to a
list): every `answers[i].answer_value` must appear verbatim in at least
one supplied fact's `value_text` — checked per item, so one fabricated
figure among several correct ones is dropped alone rather than discarding
an otherwise-good answer. `used_fff_ids` is filtered to ids that actually
exist among the supplied facts. If `answerable: true` survives with zero
valid answers or zero valid `used_fff_ids`, the whole call is retried
(bounded), then falls back to `answerable: false` — a generator that can't
ground its answer must abstain, never guess.
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
    """Never raises on a guard failure — falls back to `answerable=False`
    (bounded retry, then a deterministic fallback, never invent an
    answer)."""
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
        # answerable=true but nothing survived the guard -- retry, hoping
        # for a cleaner generation, rather than trust an ungrounded answer.

    return GeneratedAnswer(answerable=False, answer_text="The provided facts do not answer this question.")
