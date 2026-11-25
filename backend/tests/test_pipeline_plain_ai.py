"""online/pipeline_plain_ai.py: the plain AI baseline (step 5.1, decision
1, owner's yes given 2026-10-10). No retrieval, no facts -- the model
answers from its own knowledge alone. Tested with a fake client, no real
model call, except the one test that uses the shared llm-call fixture."""
from __future__ import annotations

import inspect

from llm.client import LLMClient
from online.pipeline_plain_ai import answer_question


class _SequencedClient:
    """Same fake used across the pipeline tests: returns each queued
    response in order, no real model call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def test_an_answerable_question_returns_the_models_answer():
    client = _SequencedClient([{
        "reasoning": "...", "answerable": True,
        "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
        "answer_text": "3M's net income was $5,777 million.",
    }])

    result = answer_question("What was 3M's net income in 2022?", client, generator_model="m")

    assert result.answerable is True
    assert result.abstained is False
    assert result.answer_text == "3M's net income was $5,777 million."
    assert len(result.answers) == 1
    assert result.answers[0].metric == "net_income"
    assert result.answers[0].value == "$5,777 million"


def test_the_model_saying_it_does_not_know_is_a_correct_abstention():
    client = _SequencedClient([{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "I don't know this figure."}])

    result = answer_question("What was 3M's net income in 2022?", client, generator_model="m")

    assert result.answerable is False
    assert result.abstained is True
    assert result.answer_text == "I don't know this figure."


def test_answerable_true_with_no_answers_is_still_an_abstention():
    """The model claims it can answer but gives nothing usable -- never
    trusted blindly, treated as a safe abstention."""
    client = _SequencedClient([{"reasoning": "...", "answerable": True, "answers": [], "answer_text": "..."}])

    result = answer_question("What was 3M's net income in 2022?", client, generator_model="m")

    assert result.answerable is False
    assert result.abstained is True


def test_a_schema_invalid_response_is_a_safe_abstention_not_a_crash():
    client = _SequencedClient([{"answerable": "not-a-bool"}])  # wrong type, missing required "reasoning"

    result = answer_question("A question.", client, generator_model="m")

    assert result.abstained is True
    assert result.answerable is False


def test_the_client_raising_is_a_safe_abstention_not_a_crash():
    class _RaisingClient:
        def complete(self, payload):
            raise RuntimeError("the provider is down")

    result = answer_question("A question.", _RaisingClient(), generator_model="m")

    assert result.abstained is True
    assert result.answerable is False


def test_the_generator_model_name_is_carried_through():
    client = _SequencedClient([{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "..."}])

    result = answer_question("A question.", client, generator_model="gpt-4.1-mini-2025-04-14")

    assert result.generator_model == "gpt-4.1-mini-2025-04-14"


def test_the_question_is_reported_back_unchanged():
    client = _SequencedClient([{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "..."}])

    result = answer_question("the exact question text", client, generator_model="m")

    assert result.question == "the exact question text"


def test_sends_the_exact_question_text_in_the_payload():
    client = _SequencedClient([{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "..."}])

    answer_question("the exact question text", client, generator_model="m")

    assert client.calls[0]["question"] == "the exact question text"


# --- the plan's own "zero retrieval calls" test, satisfied structurally ---


def test_answer_question_has_no_retrieval_machinery_in_its_signature():
    """The plan's own test row: "Zero retrieval calls". Proven structurally
    here, not behaviourally -- this function has no bundle, embedder,
    reranker or registry parameter at all, so it is impossible for it to
    call retrieval, not just unlikely. A mock that happens to go unused
    would only prove today's implementation never calls it; this proves
    the capability itself was never given to it."""
    params = set(inspect.signature(answer_question).parameters)

    assert params == {"question", "llm_client", "generator_model"}
    assert "bundle" not in params
    assert "embedder" not in params
    assert "reranker" not in params
    assert "registry" not in params


# --- the shared LLM-call fixture, not ad hoc patching ---


def test_exactly_one_real_llm_call_with_the_shared_fixture(llm_call_counter, payload_capture):
    """Uses the project's one shared patch point for "how many LLM calls
    happened" (tests/conftest.py), the same convention every other
    zero/one-call-count test in this project follows, instead of a
    hand-rolled monkeypatch that could silently check the wrong thing."""
    answer_question("What was 3M's net income in 2022?", LLMClient(), generator_model="m")

    assert llm_call_counter.count == 1
    assert payload_capture[0]["question"] == "What was 3M's net income in 2022?"
