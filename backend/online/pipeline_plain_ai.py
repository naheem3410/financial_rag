"""Plain AI baseline (step 5.1, decision 1, call site: plain_ai_baseline,
owner's yes given 2026-10-10): the model answers a question using only its
own training knowledge -- no retrieval, no evidence, nothing grounded.
This is the floor every other setup (C0, C4+) is measured against, never
an answer path a real user would see.

Deliberately has no `bundle`/`embedder`/`reranker`/`registry` parameter at
all, unlike every other pipeline in this project -- so it is structurally
impossible for this to touch retrieval, not just behaviourally unlikely.
The plan's own "zero retrieval calls" test (step 5.1's test table) is
satisfied by this function's own shape, not by a mock that merely happens
to go unused.

The model's own answer is trusted directly, with no numeric-verbatim
guard and no fact-grounding check -- the opposite of every other call
site in this project. That is the entire point: there is no evidence here
to check an answer against.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from llm.client import LLMClient
from llm.schemas import PlainAIOutput
from online.output.generator import AnswerValue

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You answer a financial question using only what you already know. You
have not been given any document or retrieved fact -- answer from your
own training knowledge, or say you don't know.

RULES
- If you do not know the answer with real confidence, set `answerable` to
  false and leave `answers` empty. An honest "I don't know" is correct
  here too -- a guess that sounds sure is worse than no answer.
- If you do answer, give the plain numeric value and its unit or scale.
"""


@dataclass(frozen=True)
class PlainAIAnswerResult:
    question: str
    answerable: bool
    abstained: bool
    answers: list[AnswerValue] = field(default_factory=list)
    answer_text: str = ""
    generator_model: str = ""


def answer_question(question: str, llm_client: LLMClient, generator_model: str) -> PlainAIAnswerResult:
    """One call, no retry -- there's no evidence to re-check against, so a
    bounded retry (the pattern every grounded call site uses) wouldn't
    change anything here. Any bad or unreadable response is a safe
    abstention, never a crash."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "question": question,
    }
    try:
        raw_response = llm_client.complete(payload)
        resolution = PlainAIOutput.model_validate(raw_response)
    except Exception:
        return PlainAIAnswerResult(
            question=question, answerable=False, abstained=True,
            answer_text="The model's response could not be read.", generator_model=generator_model,
        )

    if not resolution.answerable or not resolution.answers:
        return PlainAIAnswerResult(
            question=question, answerable=False, abstained=True,
            answer_text=resolution.answer_text, generator_model=generator_model,
        )

    answers = [AnswerValue(metric=a.metric, value=a.answer_value, unit=a.answer_unit) for a in resolution.answers]
    return PlainAIAnswerResult(
        question=question, answerable=True, abstained=False,
        answers=answers, answer_text=resolution.answer_text, generator_model=generator_model,
    )
