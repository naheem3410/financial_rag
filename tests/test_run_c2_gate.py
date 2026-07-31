"""C2 gate scoring/cost-tracking tests. Must PASS. Scale-detection and
by-value exact-match logic are already covered by tests/test_run_c1_gate.py
(reused directly, `is_exact_match` here is the same logic against
`C2AnswerResult`) — not repeated. This file covers what's actually new in
`evaluation/run_c2_gate.py`: `is_exact_match` duck-typing against
`C2AnswerResult`, and `_tracked_client`'s **mixed-model cost accounting** —
C2 bills gpt-4.1-mini (FFF/Generator) and gpt-5.6-luna (CoVe) within the
same question, so a per-call price (keyed to that call's own model) must
never get smeared across the other model's tokens.
"""
from __future__ import annotations

import threading

from evaluation.run_c2_gate import _tracked_client, is_exact_match
from offline.pilot import SpendCapExceeded, estimate_cost_usd
from online.output.generator import AnswerValue
from online.pipeline_c2 import C2AnswerResult


class _FakeAdapterResponse:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeRoutedClient:
    """Mimics RoutedLLMClient's `.complete()` + `.last_response_meta` shape
    (llm/routing.py) closely enough for `_tracked_client` to wrap it."""

    def __init__(self, input_tokens, output_tokens):
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.last_response_meta = None

    def complete(self, payload):
        self.last_response_meta = _FakeAdapterResponse(self._input_tokens, self._output_tokens)
        return {}


def _make_c2_result(abstained=False, answerable=True, answers=None) -> C2AnswerResult:
    return C2AnswerResult(
        question="q", abstained=abstained, abstention_message="", answerable=answerable,
        answers=answers or [], answer_text="", citations=[], used_fff_ids=[],
        retrieved_page_count=1, fff_count=1, fff_rewriter_model="m", generator_model="m",
        cove_verification_model="gpt-5.6-luna", verification_trace=[],
    )


def test_is_exact_match_works_on_c2_result():
    result = _make_c2_result(answers=[AnswerValue(metric="net_sales", value="$32,765 million", unit="millions")])
    gold = {"net_sales": 32765}

    assert is_exact_match(result, gold, "millions") is True


def test_is_exact_match_false_when_c2_abstained():
    result = _make_c2_result(abstained=True, answerable=False, answers=[])
    assert is_exact_match(result, {"net_sales": 32765}, "millions") is False


def test_tracked_client_prices_each_call_at_its_own_models_rate():
    """A gpt-4.1-mini call and a gpt-5.6-luna call with identical token
    counts must NOT cost the same -- gpt-5.6-luna is 2.5x/3.75x pricier
    (offline/pilot.py:_PRICING_PER_1M_USD). Both calls share one `usage`
    dict (as real C2 questions do, across fff/generator/cove clients), so
    this asserts the mixing doesn't collapse to a single price."""
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
    usage_lock = threading.Lock()
    spend_state = {"spent": 0.0}

    mini_client = _tracked_client(
        _FakeRoutedClient(1_000_000, 1_000_000), usage, usage_lock, spend_state, max_spend_usd=100.0,
        label="q1", model="gpt-4.1-mini-2025-04-14",
    )
    luna_client = _tracked_client(
        _FakeRoutedClient(1_000_000, 1_000_000), usage, usage_lock, spend_state, max_spend_usd=100.0,
        label="q1", model="gpt-5.6-luna",
    )

    mini_client.complete({})
    luna_client.complete({})

    expected_mini_cost = estimate_cost_usd(1_000_000, 1_000_000, model="gpt-4.1-mini-2025-04-14")
    expected_luna_cost = estimate_cost_usd(1_000_000, 1_000_000, model="gpt-5.6-luna")

    assert expected_luna_cost > expected_mini_cost  # sanity: luna really is pricier
    assert abs(usage["cost_usd"] - (expected_mini_cost + expected_luna_cost)) < 1e-9
    assert usage["input_tokens"] == 2_000_000
    assert usage["output_tokens"] == 2_000_000
    assert abs(spend_state["spent"] - usage["cost_usd"]) < 1e-9


def test_tracked_client_raises_spend_cap_exceeded_using_the_right_model_price():
    """A cap that gpt-4.1-mini pricing alone would never hit must still
    trip once a gpt-5.6-luna call's own (higher) price is added — proof the
    cap check uses the per-call price, not a stale single-model constant."""
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
    usage_lock = threading.Lock()
    # A cap between the two models' costs for the same token volume.
    mini_cost = estimate_cost_usd(1_000_000, 1_000_000, model="gpt-4.1-mini-2025-04-14")
    luna_cost = estimate_cost_usd(1_000_000, 1_000_000, model="gpt-5.6-luna")
    cap = (mini_cost + luna_cost) / 2
    spend_state = {"spent": 0.0}

    mini_client = _tracked_client(
        _FakeRoutedClient(1_000_000, 1_000_000), usage, usage_lock, spend_state, max_spend_usd=cap,
        label="q1", model="gpt-4.1-mini-2025-04-14",
    )
    luna_client = _tracked_client(
        _FakeRoutedClient(1_000_000, 1_000_000), usage, usage_lock, spend_state, max_spend_usd=cap,
        label="q1", model="gpt-5.6-luna",
    )

    mini_client.complete({})  # under cap alone
    try:
        luna_client.complete({})
        assert False, "expected SpendCapExceeded"
    except SpendCapExceeded:
        pass
