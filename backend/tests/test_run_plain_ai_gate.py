"""Plain AI baseline harness tests (step 5.1). Mirrors test_run_c2_gate.py's
scope: the pure scoring/cost-tracking/checkpoint helpers, not the outer
loop that calls build_llm_client for real (no test file in this project
exercises that part directly -- see test_run_c1_gate.py/test_run_c2_gate.py)."""
from __future__ import annotations

import json
import threading

from evaluation.run_plain_ai_gate import (
    PlainAIQuestionRecord,
    _load_checkpoint,
    _save_checkpoint,
    _tracked_client,
    is_exact_match,
    run_plain_ai_gate,
    summarize,
)
from offline.pilot import SpendCapExceeded, estimate_cost_usd
from online.output.generator import AnswerValue
from online.pipeline_plain_ai import PlainAIAnswerResult


class _FakeAdapterResponse:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeRoutedClient:
    def __init__(self, input_tokens, output_tokens):
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.last_response_meta = None

    def complete(self, payload):
        self.last_response_meta = _FakeAdapterResponse(self._input_tokens, self._output_tokens)
        return {}


def _make_result(abstained=False, answerable=True, answers=None) -> PlainAIAnswerResult:
    return PlainAIAnswerResult(
        question="q", abstained=abstained, answerable=answerable, answers=answers or [],
        answer_text="", generator_model="m",
    )


# --- is_exact_match ---


def test_is_exact_match_true_when_the_value_is_reproduced():
    result = _make_result(answers=[AnswerValue(metric="net_income", value="$5,777 million", unit="millions")])

    assert is_exact_match(result, {"net_income": 5777}, "millions") is True


def test_is_exact_match_false_when_abstained():
    result = _make_result(abstained=True, answerable=False, answers=[])

    assert is_exact_match(result, {"net_income": 5777}, "millions") is False


def test_is_exact_match_false_when_the_value_is_wrong():
    result = _make_result(answers=[AnswerValue(metric="net_income", value="$1 million", unit="millions")])

    assert is_exact_match(result, {"net_income": 5777}, "millions") is False


# --- _tracked_client ---


def test_tracked_client_accumulates_cost_and_tokens():
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
    usage_lock = threading.Lock()
    spend_state = {"spent": 0.0}

    client = _tracked_client(
        _FakeRoutedClient(1000, 500), usage, usage_lock, spend_state, max_spend_usd=100.0,
        label="q1", model="gpt-4.1-mini-2025-04-14",
    )
    client.complete({})

    expected_cost = estimate_cost_usd(1000, 500, model="gpt-4.1-mini-2025-04-14")
    assert usage["input_tokens"] == 1000
    assert usage["output_tokens"] == 500
    assert usage["requests"] == 1
    assert abs(usage["cost_usd"] - expected_cost) < 1e-9
    assert abs(spend_state["spent"] - expected_cost) < 1e-9


def test_tracked_client_raises_spend_cap_exceeded():
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
    usage_lock = threading.Lock()
    spend_state = {"spent": 0.0}
    client = _tracked_client(
        _FakeRoutedClient(1_000_000, 1_000_000), usage, usage_lock, spend_state, max_spend_usd=0.0001,
        label="q1", model="gpt-4.1-mini-2025-04-14",
    )

    try:
        client.complete({})
        assert False, "expected SpendCapExceeded"
    except SpendCapExceeded:
        pass


# --- checkpoint load/save ---


def test_checkpoint_round_trips(tmp_path):
    path = tmp_path / "checkpoint.json"
    state = {"completed": {"c_1": {"collision_id": "c_1"}}}

    _save_checkpoint(path, state)
    loaded = _load_checkpoint(path)

    assert loaded == state


def test_missing_checkpoint_starts_empty(tmp_path):
    loaded = _load_checkpoint(tmp_path / "does_not_exist.json")

    assert loaded == {"completed": {}}


def test_run_plain_ai_gate_resumes_from_checkpoint_without_any_client(tmp_path):
    """Step 5.3's own "stop half way, restart" test, exercised here too:
    an item already in the checkpoint is returned as-is -- it never
    reaches build_llm_client (and so never spends again), proven simply by
    there being no config/client available for it to reach in this test."""
    path = tmp_path / "checkpoint.json"
    completed_record = PlainAIQuestionRecord(
        collision_id="c_1", question="q", verification_status="verified", exact_match=True,
        abstained=False, answerable=True, answers=[{"metric": "net_income", "value": "$1", "unit": "millions"}],
        answer_text="a", citations=[], retrieved_chunk_count=0, generator_model="m",
        input_tokens=10, output_tokens=5, cost_usd=0.001, elapsed_seconds=0.1,
    )
    _save_checkpoint(path, {"completed": {"c_1": completed_record.__dict__}})

    results = run_plain_ai_gate(
        records=[{"collision_id": "c_1", "question": "q", "verification_status": "verified", "gold_answers": {}, "scale": "millions"}],
        config=None, checkpoint_path=path, max_spend_usd=100.0,  # config=None is fine -- never reached
    )

    assert len(results) == 1
    assert results[0].collision_id == "c_1"
    assert results[0].cost_usd == 0.001


# --- summarize ---


def test_summarize_reports_accuracy_and_cost():
    results = [
        PlainAIQuestionRecord(
            collision_id="c_1", question="q1", verification_status="verified", exact_match=True,
            abstained=False, answerable=True, answers=[], answer_text="a", citations=[],
            retrieved_chunk_count=0, generator_model="m", input_tokens=10, output_tokens=5,
            cost_usd=0.01, elapsed_seconds=0.1,
        ),
        PlainAIQuestionRecord(
            collision_id="c_2", question="q2", verification_status="verified", exact_match=False,
            abstained=True, answerable=False, answers=[], answer_text="", citations=[],
            retrieved_chunk_count=0, generator_model="m", input_tokens=8, output_tokens=2,
            cost_usd=0.005, elapsed_seconds=0.1,
        ),
    ]

    summary = summarize(results)

    assert summary["n_questions"] == 2
    assert summary["n_scored_verified"] == 2
    assert summary["exact_match_accuracy_verified_only"] == 0.5
    assert summary["negative_rejection_rate"] == 0.5
    assert abs(summary["total_cost_usd"] - 0.015) < 1e-9
    assert summary["total_input_tokens"] == 18
    assert summary["total_output_tokens"] == 7
