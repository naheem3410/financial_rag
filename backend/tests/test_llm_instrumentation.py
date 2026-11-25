"""Meta-tests for the llm_call_counter / payload_capture fixtures.
Must pass today -- the four load-bearing zero-LLM-call tests are only
trustworthy if these fixtures actually observe LLMClient.complete."""
from __future__ import annotations

from llm.client import LLMClient


def test_llm_call_counter_is_zero_with_no_calls(llm_call_counter):
    assert llm_call_counter.count == 0


def test_llm_call_counter_counts_a_deliberate_call(llm_call_counter):
    client = LLMClient()

    client.complete({"schema_version": "1.0", "probe": "value"})

    assert llm_call_counter.count == 1


def test_llm_call_counter_counts_multiple_calls(llm_call_counter):
    client = LLMClient()

    client.complete({"schema_version": "1.0", "n": 1})
    client.complete({"schema_version": "1.0", "n": 2})
    client.complete({"schema_version": "1.0", "n": 3})

    assert llm_call_counter.count == 3


def test_payload_capture_is_empty_with_no_calls(payload_capture):
    assert payload_capture == []


def test_payload_capture_exposes_the_call_payload(payload_capture):
    client = LLMClient()
    payload = {"schema_version": "1.0", "probe": "value", "n": 42}

    client.complete(payload)

    assert len(payload_capture) == 1
    assert payload_capture[0] == payload


def test_payload_capture_preserves_call_order(payload_capture):
    client = LLMClient()

    client.complete({"schema_version": "1.0", "step": "first"})
    client.complete({"schema_version": "1.0", "step": "second"})

    assert [p["step"] for p in payload_capture] == ["first", "second"]


def test_llm_call_counter_and_payload_capture_share_one_boundary(
    llm_call_counter, payload_capture
):
    """Both fixtures share one instrumentation boundary -- no double-patching,
    no blind spots."""
    client = LLMClient()

    client.complete({"schema_version": "1.0", "a": 1})
    client.complete({"schema_version": "1.0", "a": 2})

    assert llm_call_counter.count == 2
    assert len(payload_capture) == 2


def test_unpatched_llm_client_raises_not_implemented():
    """Sanity check on the stub itself: outside the fixtures, the Phase 0
    seam has no provider and must not silently succeed."""
    import pytest

    client = LLMClient()
    with pytest.raises(NotImplementedError):
        client.complete({"schema_version": "1.0"})
