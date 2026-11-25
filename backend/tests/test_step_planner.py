"""online/routing/step_planner.py: step by step (step 4.6), the seventh
call site. `validate_step_plan` and `substitute_step_answers` are pure
code, no model; `plan_steps` is the model-calling entry point, tested here
with a fake client, no real model call."""
from __future__ import annotations

from online.routing.step_planner import (
    MAX_STEPS,
    plan_steps,
    substitute_step_answers,
    validate_step_plan,
)


class _SequencedClient:
    """Same fake used across the pipeline tests: returns each queued
    response in order, no real model call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def _steps(*pairs):
    """(step_id, question) pairs -> the raw dict shape validate_step_plan expects."""
    return [{"step_id": sid, "question": q} for sid, q in pairs]


# --- validate_step_plan ---


def test_a_two_step_plan_is_accepted_in_order():
    raw = _steps((1, "Which company had the highest revenue in 2022?"), (2, "What was {step_1}'s net income in 2022?"))

    result = validate_step_plan(raw)

    assert result is not None
    assert [s.step_id for s in result.steps] == [1, 2]
    assert result.capped is False


def test_an_empty_plan_is_rejected():
    result = validate_step_plan([])

    assert result is None


def test_a_step_referencing_itself_is_a_loop_and_rejected():
    raw = _steps((1, "What was {step_1}'s net income?"))

    result = validate_step_plan(raw)

    assert result is None


def test_a_step_referencing_a_later_step_is_a_loop_and_rejected():
    raw = _steps((1, "What was {step_2}'s net income?"), (2, "Which company had the highest revenue?"))

    result = validate_step_plan(raw)

    assert result is None


def test_a_step_referencing_an_earlier_step_is_fine():
    raw = _steps(
        (1, "Which company had the highest revenue in 2022?"),
        (2, "What was {step_1}'s net income in 2022?"),
        (3, "What was {step_1}'s net income divided by {step_2}?"),
    )

    result = validate_step_plan(raw)

    assert result is not None
    assert len(result.steps) == 3


def test_a_gap_in_step_ids_is_rejected():
    raw = _steps((1, "Question one?"), (3, "Question three?"))

    result = validate_step_plan(raw)

    assert result is None


def test_a_duplicate_step_id_is_rejected():
    raw = _steps((1, "Question one?"), (1, "Question one again?"))

    result = validate_step_plan(raw)

    assert result is None


def test_out_of_order_step_ids_are_rejected():
    raw = _steps((2, "Question two?"), (1, "Question one?"))

    result = validate_step_plan(raw)

    assert result is None


def test_too_many_steps_is_capped_not_rejected():
    raw = _steps(*[(i, f"Question {i}?") for i in range(1, MAX_STEPS + 3)])

    result = validate_step_plan(raw)

    assert result is not None
    assert len(result.steps) == MAX_STEPS
    assert result.capped is True


def test_exactly_the_cap_is_not_marked_capped():
    raw = _steps(*[(i, f"Question {i}?") for i in range(1, MAX_STEPS + 1)])

    result = validate_step_plan(raw)

    assert result is not None
    assert result.capped is False


def test_a_single_step_plan_is_accepted():
    raw = _steps((1, "What was 3M's net income in 2022?"))

    result = validate_step_plan(raw)

    assert result is not None
    assert len(result.steps) == 1


# --- substitute_step_answers ---


def test_substitutes_a_single_reference():
    result = substitute_step_answers("What was {step_1}'s net income?", {1: "3M"})

    assert result == "What was 3M's net income?"


def test_substitutes_multiple_references():
    result = substitute_step_answers("Compare {step_1} to {step_2}.", {1: "3M", 2: "Adobe"})

    assert result == "Compare 3M to Adobe."


def test_a_reference_with_no_matching_answer_is_left_as_is():
    result = substitute_step_answers("What was {step_5}'s net income?", {1: "3M"})

    assert result == "What was {step_5}'s net income?"


def test_a_question_with_no_references_is_untouched():
    result = substitute_step_answers("What was 3M's net income?", {1: "3M"})

    assert result == "What was 3M's net income?"


# --- plan_steps: the model-calling entry point ---


def test_plan_steps_accepts_a_valid_plan_from_the_model():
    client = _SequencedClient([{
        "reasoning": "...",
        "steps": [
            {"step_id": 1, "question": "Which company had the highest revenue in 2022?"},
            {"step_id": 2, "question": "What was {step_1}'s net income in 2022?"},
        ],
    }])

    result = plan_steps("What was the net income of the company with the highest revenue in 2022?", client)

    assert result is not None
    assert len(result.steps) == 2


def test_plan_steps_rejects_a_loop_from_the_model():
    client = _SequencedClient([{
        "reasoning": "...",
        "steps": [{"step_id": 1, "question": "What was {step_1}'s net income?"}],
    }])

    result = plan_steps("A confusing question.", client)

    assert result is None


def test_plan_steps_empty_steps_is_a_correct_no_plan_answer():
    client = _SequencedClient([{"reasoning": "...", "steps": []}])

    result = plan_steps("What was 3M's net income in 2022?", client)

    assert result is None


def test_plan_steps_schema_invalid_response_is_dropped_not_guessed():
    client = _SequencedClient([{"steps": "not-a-list"}])  # wrong type, missing required "reasoning"

    result = plan_steps("A question.", client)

    assert result is None


def test_plan_steps_sends_the_question_text():
    client = _SequencedClient([{"reasoning": "...", "steps": []}])

    plan_steps("the exact question text", client)

    assert client.calls[0]["question"] == "the exact question text"
