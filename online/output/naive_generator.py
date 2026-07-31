"""Naive generator — C0 only.

Reuses the generator's config slot (`generator` in
`config/experiments/c0_naive.yaml`) via `schema_key="naive_generator"`
(`llm/routing.py`) — not a separate call site, the same functional role
(turn retrieved evidence into a final answer) with a genuinely different,
un-guarded prompt for this cell specifically.

No FFF atomization, no numeric-verbatim guard — deliberately. C1-C4's
`online/output/generator.py:generate_answer` receives atomic, guarded
Fully-Formatted Facts and still checks every emitted value appears
verbatim in a supplied fact. C0 is the reference point for what a
standard RAG pipeline looks like without any of that: raw retrieved chunk
text stuffed directly into the prompt, whatever the model asserts is
trusted as-is. Skipping the guard here is not an oversight — an honest C0
has to be able to hallucinate, or it isn't measuring what the guards in
C1-C4 actually buy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from contracts.models import Proposition
from llm.client import LLMClient
from online.output.generator import AnswerValue

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You answer a financial question using the passages provided below.

The passages are raw excerpts from a filing. They may contain more information than
you need, may be incomplete, or may not contain the answer at all.

RULES
- Use only the passages provided. Do not use prior knowledge about this company or
  any other.
- If the passages do not contain the answer, set `answerable` to false.
- The question may ask for more than one metric -- emit one entry in `answers` per
  metric actually asked, if you can find it in the passages.
"""


@dataclass(frozen=True)
class NaiveGeneratedAnswer:
    answerable: bool
    answers: list[AnswerValue] = field(default_factory=list)
    answer_text: str = ""
    used_chunk_ids: list[str] = field(default_factory=list)


def _generate_once(question: str, chunks: Sequence[Proposition], llm_client: LLMClient) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "question": question,
        "passages": [{"chunk_id": c.proposition_id, "text": c.text} for c in chunks],
    }
    return llm_client.complete(payload)


def generate_naive_answer(
    question: str,
    chunks: Sequence[Proposition],
    llm_client: LLMClient,
) -> NaiveGeneratedAnswer:
    """C0's generator variant: no atomization, no numeric-verbatim guard —
    see module docstring for why that's the honest baseline, not a gap."""
    if not chunks:
        return NaiveGeneratedAnswer(
            answerable=False, answer_text="No supporting passages were retrieved for this question."
        )

    chunk_ids = {c.proposition_id for c in chunks}
    response = _generate_once(question, chunks, llm_client)

    if not response.get("answerable", False):
        return NaiveGeneratedAnswer(
            answerable=False,
            answer_text=response.get("answer_text", "The provided passages do not answer this question."),
        )

    answers = [
        AnswerValue(metric=a.get("metric", ""), value=a.get("answer_value", ""), unit=a.get("answer_unit", ""))
        for a in response.get("answers", [])
    ]
    used_chunk_ids = [cid for cid in response.get("used_chunk_ids", []) if cid in chunk_ids]

    return NaiveGeneratedAnswer(
        answerable=bool(answers),
        answers=answers,
        answer_text=response.get("answer_text", ""),
        used_chunk_ids=used_chunk_ids,
    )
