"""LLM routing tests. No real network calls — fake adapters exercise
`RoutedLLMClient`'s own logic; `build_llm_client` wiring is checked against
real provider construction (safe: SDK clients don't touch the network until
a call is made) without ever calling `.complete()`.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from config.schema import ExperimentConfig
from llm.adapters.base import AdapterResponse
from llm.rate_limiter import RateLimiter
from llm.routing import RoutedLLMClient, build_llm_client
from llm.schemas import PropositionSegmenterOutput, PropositionSegmenterOutputNoReasoning

_ALL_CALL_SITES = {
    "proposition_segmenter": {
        "provider": "openai", "model": "gpt-4.1-mini-2025-04-14",
        "concurrency": 16, "include_reasoning_field": False,
    },
    "numeric_qualifier_resolver": {"provider": "openai", "model": "m-2025-01-01"},
    "query_splitter": {"provider": "openai", "model": "m-2025-01-01"},
    "fff_rewriter": {"provider": "openai", "model": "m-2025-01-01"},
    "generator": {"provider": "openai", "model": "m-2025-01-01"},
    "cove_verification": {"provider": "openai", "model": "m-2025-01-01"},
}

_BASE_CONFIG = dict(
    experiment_id="c1_baseline",
    enable_npce=False,
    enable_cove=False,
    retrieval=dict(mode="hybrid", top_k=20, rerank_top_k=5, enable_reranker=True, time_weighting=True),
    indexing=dict(granularity="proposition"),
    llm=dict(
        defaults={"temperature": 0.0, "structured_output": True},
        call_sites=_ALL_CALL_SITES,
        providers={"openai": {"api_key_env": "OPENAI_API_KEY", "rpm": 500, "tpm": 200000, "rpd": 10000}},
    ),
)


class _FakeAdapter:
    def __init__(self, response_data: dict):
        self._response_data = response_data
        self.calls: list[dict] = []

    def complete(self, messages, *, model, temperature, seed, response_schema):
        self.calls.append(
            {"messages": messages, "model": model, "temperature": temperature, "seed": seed, "schema": response_schema}
        )
        return AdapterResponse(
            data=self._response_data, seed_supported=False, reasoning_tokens=None,
            input_tokens=10, output_tokens=5, adapter_version="fake-1.0",
        )


class TestRoutedLLMClient:
    def test_complete_splits_system_prompt_from_payload(self):
        adapter = _FakeAdapter({"propositions": []})
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=None, response_schema=PropositionSegmenterOutputNoReasoning
        )

        client.complete({"system_prompt": "You segment text.", "source_span": "some text"})

        sent = adapter.calls[0]["messages"]
        assert sent[0] == {"role": "system", "content": "You segment text."}
        assert "source_span" in sent[1]["content"]
        assert "system_prompt" not in sent[1]["content"]

    def test_complete_returns_adapter_data_unchanged(self):
        adapter = _FakeAdapter({"propositions": [{"text": "x", "source_offset_hint": None}]})
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=None, response_schema=PropositionSegmenterOutputNoReasoning
        )

        result = client.complete({"system_prompt": "sys"})

        assert result == {"propositions": [{"text": "x", "source_offset_hint": None}]}

    def test_complete_records_response_meta_for_telemetry(self):
        adapter = _FakeAdapter({"propositions": []})
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=None, response_schema=PropositionSegmenterOutputNoReasoning
        )

        client.complete({"system_prompt": "sys"})

        assert client.last_response_meta.input_tokens == 10
        assert client.last_response_meta.output_tokens == 5

    def test_complete_acquires_rate_limiter_before_calling_adapter(self):
        adapter = _FakeAdapter({"propositions": []})
        limiter = RateLimiter(rpm=1000)
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutputNoReasoning, rate_limiter=limiter,
        )

        client.complete({"system_prompt": "sys"})

        assert len(limiter._request_times) == 1

    def test_complete_feeds_a_real_token_estimate_into_the_tpm_limiter(self):
        """Regression: acquire() must not always be called with
        estimated_tokens=0 — that would make the TPM cap inert regardless
        of how large the payload actually is."""
        adapter = _FakeAdapter({"propositions": []})
        limiter = RateLimiter(tpm=1000)
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="gpt-4.1-mini-2025-04-14", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutputNoReasoning, rate_limiter=limiter,
        )

        client.complete({"system_prompt": "sys", "source_span": "word " * 500})

        used = sum(tokens for _, tokens in limiter._token_events)
        assert used > 0

    def test_current_tpm_usage_delegates_to_the_rate_limiter(self):
        adapter = _FakeAdapter({"propositions": []})
        limiter = RateLimiter(tpm=200000)
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutputNoReasoning, rate_limiter=limiter,
        )

        client.complete({"system_prompt": "sys", "source_span": "some text"})

        assert client.current_tpm_usage() == limiter.current_tpm_usage()
        assert client.current_tpm_usage() > 0

    def test_current_tpm_usage_is_none_without_a_rate_limiter(self):
        adapter = _FakeAdapter({"propositions": []})
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutputNoReasoning,
        )

        assert client.current_tpm_usage() is None

    def test_to_manifest_before_any_call_raises(self):
        adapter = _FakeAdapter({"propositions": []})
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=42,
            response_schema=PropositionSegmenterOutputNoReasoning,
        )

        with pytest.raises(RuntimeError, match="has not completed a call"):
            client.to_manifest("proposition_segmenter")

    def test_to_manifest_after_a_call_records_provider_model_seed(self):
        adapter = _FakeAdapter({"propositions": []})
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=42,
            response_schema=PropositionSegmenterOutputNoReasoning, include_reasoning_field=False,
        )
        client.complete({"system_prompt": "sys"})

        manifest = client.to_manifest("proposition_segmenter")

        assert manifest.call_site == "proposition_segmenter"
        assert manifest.provider == "fake"
        assert manifest.model == "m"
        assert manifest.seed == 42
        assert manifest.include_reasoning_field is False
        assert manifest.adapter_version == "fake-1.0"
        assert manifest.seed_supported is False


class _SlowVariableUsageAdapter:
    """Returns a distinct, larger token count per call (call N gets
    `base + N` tokens) with a small sleep to force overlap when called from
    multiple threads — used to prove `last_response_meta` cannot leak
    another thread's response."""

    def __init__(self, delay_seconds: float = 0.02):
        self._delay_seconds = delay_seconds
        self._lock = threading.Lock()
        self._call_index = 0

    def complete(self, messages, *, model, temperature, seed, response_schema):
        with self._lock:
            self._call_index += 1
            call_index = self._call_index
        time.sleep(self._delay_seconds)
        return AdapterResponse(
            data={"propositions": []}, seed_supported=False, reasoning_tokens=None,
            input_tokens=1000 + call_index, output_tokens=100 + call_index, adapter_version="fake-1.0",
        )


class TestRoutedLLMClientThreadSafety:
    """Regression, 2026-07-25: `segment_pages`'s `max_workers>1` path shares
    one `RoutedLLMClient` across threads. `last_response_meta` used to be a
    single mutable attribute — one thread's read could race another
    thread's write, corrupting cost accounting in `offline/pilot.py`."""

    def test_last_response_meta_never_leaks_across_threads(self):
        adapter = _SlowVariableUsageAdapter(delay_seconds=0.02)
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutputNoReasoning,
        )

        observed_input_tokens: list[int] = []
        lock = threading.Lock()

        def worker():
            client.complete({"system_prompt": "sys"})
            meta = client.last_response_meta
            # The adapter always returns output_tokens == input_tokens - 900
            # (both derived from the same call_index) -- if last_response_meta
            # ever returned a DIFFERENT thread's response, this pairing
            # would be broken for at least one observation.
            with lock:
                observed_input_tokens.append(meta.input_tokens)
                assert meta.output_tokens == meta.input_tokens - 900

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 20 calls, each thread's own call_index reflected back consistently
        # -- and every call_index appears exactly once (no call's tokens
        # were read twice or lost).
        assert sorted(observed_input_tokens) == [1000 + i for i in range(1, 21)]

    def test_to_manifest_is_populated_after_concurrent_calls(self):
        """`to_manifest` reads adapter_version/seed_supported from whichever
        thread completed a call — those fields are invariant across calls,
        so any completed call's value is representative, even if the main
        thread itself never called .complete()."""
        adapter = _SlowVariableUsageAdapter(delay_seconds=0.01)
        client = RoutedLLMClient(
            adapter=adapter, provider_name="fake", model="m", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutputNoReasoning,
        )

        def worker():
            client.complete({"system_prompt": "sys"})

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        manifest = client.to_manifest("proposition_segmenter")  # called from the main thread
        assert manifest.adapter_version == "fake-1.0"


class TestBuildLLMClient:
    def test_wires_model_and_temperature_from_config(self):
        config = ExperimentConfig.model_validate(_BASE_CONFIG)

        client = build_llm_client("proposition_segmenter", config)

        assert client._model == "gpt-4.1-mini-2025-04-14"
        assert client._temperature == 0.0

    def test_selects_no_reasoning_schema_when_configured(self):
        config = ExperimentConfig.model_validate(_BASE_CONFIG)

        client = build_llm_client("proposition_segmenter", config)

        assert client._response_schema is PropositionSegmenterOutputNoReasoning

    def test_selects_with_reasoning_schema_by_default(self):
        config = ExperimentConfig.model_validate(_BASE_CONFIG)

        client = build_llm_client("numeric_qualifier_resolver", config)

        # numeric_qualifier_resolver uses NumericQualifierResolution, which
        # always requires `reasoning` — no no-reasoning variant exists.
        assert "reasoning" in client._response_schema.model_fields

    def test_unknown_call_site_schema_raises(self):
        # All six real call sites are registered as of 2026-07-27
        # (query_splitter wired for C3), so there's no remaining configured
        # call site name left to exercise this through build_llm_client --
        # test the schema-resolution layer directly instead, with a name
        # that could never be a real call site.
        from llm.routing import _resolve_schema

        with pytest.raises(ValueError, match="no output schema registered"):
            _resolve_schema("totally_unregistered_call_site", include_reasoning_field=True)

    def test_rate_limiter_uses_provider_config(self):
        config = ExperimentConfig.model_validate(_BASE_CONFIG)

        client = build_llm_client("proposition_segmenter", config)

        assert client._rate_limiter._rpm == 500
        assert client._rate_limiter._tpm == 200000
        assert client._rate_limiter._rpd == 10000
