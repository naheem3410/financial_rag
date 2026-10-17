"""Provider adapter tests. No real network calls; the underlying SDK
client's call method is monkeypatched so these test the adapter's own
request/response normalization logic, not the provider's live API.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from llm.adapters.base import LLMCallFailed, LLMInfrastructureError
from llm.schemas import PropositionSegmenterOutput

_FAKE_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _fake_status_error(exc_class, status_code: int, *, code=None, error_type=None, param=None, message="simulated failure"):
    """Builds an openai SDK exception the way the SDK itself does (unwrapped
    inner `error` object as `body`) so exc.code/type/param behave like the real thing."""
    response = httpx.Response(status_code, request=_FAKE_REQUEST)
    body = {"message": message, "type": error_type, "param": param, "code": code}
    return exc_class(message, response=response, body=body)


class TestOpenAIAdapter:
    def _make_adapter(self, monkeypatch):
        from llm.adapters.openai_adapter import OpenAIAdapter

        adapter = OpenAIAdapter(api_key="test-key-not-real")
        return adapter

    def test_complete_extracts_parsed_data_and_token_usage(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch)
        parsed = PropositionSegmenterOutput(
            reasoning="ok", propositions=[{"text": "Acme Corp net sales were $32.2 billion.", "source_offset_hint": None}]
        )
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
            usage=SimpleNamespace(
                prompt_tokens=120, completion_tokens=40,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
        )
        monkeypatch.setattr(adapter._client.chat.completions, "parse", lambda **kwargs: fake_response)

        result = adapter.complete(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}],
            model="gpt-4.1-mini-2025-04-14", temperature=0.0, seed=42,
            response_schema=PropositionSegmenterOutput,
        )

        assert result.data["propositions"][0]["text"] == "Acme Corp net sales were $32.2 billion."
        assert result.input_tokens == 120
        assert result.output_tokens == 40
        assert result.reasoning_tokens == 0
        assert result.seed_supported is False  # best-effort only, never guaranteed by OpenAI

    def test_reasoning_model_temperature_rejected_falls_back_and_retries(self, monkeypatch):
        """Reasoning models reject temperature=0 with a 400; adapter retries once
        at 1.0 and reports temperature_supported=False, mirroring seed_supported."""
        from llm.adapters.openai_adapter import BadRequestError

        adapter = self._make_adapter(monkeypatch)
        parsed = PropositionSegmenterOutput(reasoning="ok", propositions=[])
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, completion_tokens_details=None),
        )
        calls = []

        def _fake_parse(**kwargs):
            calls.append(kwargs["temperature"])
            if kwargs["temperature"] == 0.0:
                raise _fake_status_error(
                    BadRequestError, 400, code="unsupported_value", error_type="invalid_request_error",
                    param="temperature",
                    message="Unsupported value: 'temperature' does not support 0 with this model. "
                    "Only the default (1) value is supported.",
                )
            return fake_response

        monkeypatch.setattr(adapter._client.chat.completions, "parse", _fake_parse)

        result = adapter.complete(
            [{"role": "user", "content": "x"}], model="gpt-5.6-luna", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutput,
        )

        assert calls == [0.0, 1.0]  # requested temp first, then provider's fallback
        assert result.temperature_supported is False
        assert result.data["propositions"] == []

    def test_non_temperature_bad_request_is_not_retried(self, monkeypatch):
        """A BadRequestError on any param other than temperature is a genuine
        error, not this capability gap -- no retry, still becomes LLMCallFailed."""
        from llm.adapters.openai_adapter import BadRequestError

        adapter = self._make_adapter(monkeypatch)
        calls = []

        def _fake_parse(**kwargs):
            calls.append(kwargs["temperature"])
            raise _fake_status_error(
                BadRequestError, 400, code="unsupported_value", error_type="invalid_request_error",
                param="response_format", message="some other bad request",
            )

        monkeypatch.setattr(adapter._client.chat.completions, "parse", _fake_parse)

        with pytest.raises(LLMCallFailed):
            adapter.complete(
                [{"role": "user", "content": "x"}], model="gpt-5.6-luna", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )
        assert calls == [0.0]  # no retry

    def test_complete_raises_llm_call_failed_on_refusal(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch)
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=None, refusal="policy"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=0, completion_tokens_details=None),
        )
        monkeypatch.setattr(adapter._client.chat.completions, "parse", lambda **kwargs: fake_response)

        with pytest.raises(LLMCallFailed, match="refusal"):
            adapter.complete(
                [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

    def test_complete_handles_missing_reasoning_tokens_field(self, monkeypatch):
        """Models with no reasoning step may omit completion_tokens_details entirely; must not crash."""
        adapter = self._make_adapter(monkeypatch)
        parsed = PropositionSegmenterOutput(reasoning="ok", propositions=[])
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, completion_tokens_details=None),
        )
        monkeypatch.setattr(adapter._client.chat.completions, "parse", lambda **kwargs: fake_response)

        result = adapter.complete(
            [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutput,
        )

        assert result.reasoning_tokens is None

    def test_length_finish_reason_error_raises_llm_call_failed_with_real_usage(self, monkeypatch):
        """Regression (2026-07-25 pilot crash): hitting the output-length limit
        must convert to LLMCallFailed but keep the real token usage for cost accounting."""
        from llm.adapters.openai_adapter import LengthFinishReasonError

        adapter = self._make_adapter(monkeypatch)
        fake_completion = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=2325, completion_tokens=32768,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            )
        )

        def _raise_length_error(**kwargs):
            raise LengthFinishReasonError(completion=fake_completion)

        monkeypatch.setattr(adapter._client.chat.completions, "parse", _raise_length_error)

        with pytest.raises(LLMCallFailed) as exc_info:
            adapter.complete(
                [{"role": "user", "content": "x"}], model="gpt-4.1-mini-2025-04-14", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

        partial = exc_info.value.partial_response
        assert partial is not None
        assert partial.input_tokens == 2325
        assert partial.output_tokens == 32768
        assert partial.data == {}

    def test_content_filter_error_raises_llm_call_failed_with_no_partial_usage(self, monkeypatch):
        """Regression: a second pilot crash where routine 10-K litigation text
        tripped OpenAI's content filter, a different exception with no usage info at all."""
        from llm.adapters.openai_adapter import ContentFilterFinishReasonError

        adapter = self._make_adapter(monkeypatch)

        def _raise_content_filter_error(**kwargs):
            raise ContentFilterFinishReasonError()

        monkeypatch.setattr(adapter._client.chat.completions, "parse", _raise_content_filter_error)

        with pytest.raises(LLMCallFailed) as exc_info:
            adapter.complete(
                [{"role": "user", "content": "x"}], model="gpt-4.1-mini-2025-04-14", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

        assert exc_info.value.partial_response is None

    def test_an_unanticipated_openai_error_subclass_is_still_caught(self, monkeypatch):
        """Catching OpenAIError broadly means an unseen subclass still converts
        to LLMCallFailed instead of crashing -- no special case needed."""
        from llm.adapters.openai_adapter import OpenAIError

        adapter = self._make_adapter(monkeypatch)

        def _raise_generic_api_error(**kwargs):
            raise OpenAIError("simulated transient failure")

        monkeypatch.setattr(adapter._client.chat.completions, "parse", _raise_generic_api_error)

        with pytest.raises(LLMCallFailed):
            adapter.complete(
                [{"role": "user", "content": "x"}], model="gpt-4.1-mini-2025-04-14", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

    def test_insufficient_quota_halts_rather_than_degrades_to_empty(self, monkeypatch):
        """Regression 2026-07-25: insufficient_quota was previously swallowed as an
        ordinary LLMCallFailed. Must raise LLMInfrastructureError so the run halts instead."""
        from llm.adapters.openai_adapter import RateLimitError

        adapter = self._make_adapter(monkeypatch)
        exc = _fake_status_error(
            RateLimitError, 429, code="insufficient_quota", error_type="insufficient_quota",
            message="You exceeded your current quota, please check your plan and billing details.",
        )
        monkeypatch.setattr(adapter._client.chat.completions, "parse", lambda **kwargs: (_ for _ in ()).throw(exc))

        with pytest.raises(LLMInfrastructureError, match="quota"):
            adapter.complete(
                [{"role": "user", "content": "x"}], model="gpt-4.1-mini-2025-04-14", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

    def test_authentication_error_halts(self, monkeypatch):
        from llm.adapters.openai_adapter import AuthenticationError

        adapter = self._make_adapter(monkeypatch)
        exc = _fake_status_error(AuthenticationError, 401, code="invalid_api_key", message="Invalid API key")
        monkeypatch.setattr(adapter._client.chat.completions, "parse", lambda **kwargs: (_ for _ in ()).throw(exc))

        with pytest.raises(LLMInfrastructureError):
            adapter.complete(
                [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

    def test_permission_denied_error_halts(self, monkeypatch):
        from llm.adapters.openai_adapter import PermissionDeniedError

        adapter = self._make_adapter(monkeypatch)
        exc = _fake_status_error(PermissionDeniedError, 403, code="permission_denied", message="Forbidden")
        monkeypatch.setattr(adapter._client.chat.completions, "parse", lambda **kwargs: (_ for _ in ()).throw(exc))

        with pytest.raises(LLMInfrastructureError):
            adapter.complete(
                [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

    def test_connection_error_halts(self, monkeypatch):
        from llm.adapters.openai_adapter import APIConnectionError

        adapter = self._make_adapter(monkeypatch)
        exc = APIConnectionError(request=_FAKE_REQUEST)
        monkeypatch.setattr(adapter._client.chat.completions, "parse", lambda **kwargs: (_ for _ in ()).throw(exc))

        with pytest.raises(LLMInfrastructureError):
            adapter.complete(
                [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

    def test_ordinary_rate_limit_retries_with_backoff_then_succeeds(self, monkeypatch):
        """A single ordinary 429 (proactive throttling still races past the
        provider's own limit sometimes) must back off and retry, not halt."""
        from llm.adapters.openai_adapter import RateLimitError

        adapter = self._make_adapter(monkeypatch)
        adapter._rate_limit_backoff_base_seconds = 0.001  # keep the test fast
        sleeps: list[float] = []
        monkeypatch.setattr("llm.adapters.openai_adapter.time.sleep", lambda s: sleeps.append(s))

        ordinary_rate_limit = _fake_status_error(
            RateLimitError, 429, code="rate_limit_exceeded", error_type="requests", message="Rate limit reached"
        )
        parsed = PropositionSegmenterOutput(reasoning="ok", propositions=[])
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, completion_tokens_details=None),
        )
        calls = {"n": 0}

        def _flaky_parse(**kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise ordinary_rate_limit
            return fake_response

        monkeypatch.setattr(adapter._client.chat.completions, "parse", _flaky_parse)

        result = adapter.complete(
            [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutput,
        )

        assert calls["n"] == 3  # two failures, then success
        assert len(sleeps) == 2  # backed off before each retry
        assert result.input_tokens == 10

    def test_ordinary_rate_limit_halts_after_exhausting_retries(self, monkeypatch):
        from llm.adapters.openai_adapter import RateLimitError

        adapter = self._make_adapter(monkeypatch)
        adapter._rate_limit_backoff_base_seconds = 0.001
        adapter._rate_limit_max_retries = 2
        monkeypatch.setattr("llm.adapters.openai_adapter.time.sleep", lambda s: None)

        exc = _fake_status_error(RateLimitError, 429, code="rate_limit_exceeded", error_type="requests")
        calls = {"n": 0}

        def _always_rate_limited(**kwargs):
            calls["n"] += 1
            raise exc

        monkeypatch.setattr(adapter._client.chat.completions, "parse", _always_rate_limited)

        with pytest.raises(LLMInfrastructureError, match="rate-limited"):
            adapter.complete(
                [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )
        assert calls["n"] == 3  # initial attempt + 2 retries


class TestAnthropicAdapter:
    def _make_adapter(self):
        from llm.adapters.anthropic_adapter import AnthropicAdapter

        return AnthropicAdapter(api_key="test-key-not-real")

    def test_complete_extracts_tool_use_input_and_usage(self, monkeypatch):
        adapter = self._make_adapter()
        tool_block = SimpleNamespace(
            type="tool_use",
            input={"reasoning": "ok", "propositions": [{"text": "Acme Corp net sales were $32.2 billion.", "source_offset_hint": None}]},
        )
        fake_response = SimpleNamespace(
            content=[tool_block],
            usage=SimpleNamespace(input_tokens=80, output_tokens=30),
        )
        monkeypatch.setattr(adapter._client.messages, "create", lambda **kwargs: fake_response)

        result = adapter.complete(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}],
            model="claude-x", temperature=0.0, seed=42, response_schema=PropositionSegmenterOutput,
        )

        assert result.data["propositions"][0]["text"] == "Acme Corp net sales were $32.2 billion."
        assert result.input_tokens == 80
        assert result.output_tokens == 30
        assert result.seed_supported is False  # Anthropic has no seed parameter at all

    def test_complete_raises_llm_call_failed_when_no_tool_use_block_returned(self, monkeypatch):
        adapter = self._make_adapter()
        fake_response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="I decided not to call the tool")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        monkeypatch.setattr(adapter._client.messages, "create", lambda **kwargs: fake_response)

        with pytest.raises(LLMCallFailed, match="tool_use"):
            adapter.complete(
                [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

    def test_complete_never_sends_a_seed_parameter(self, monkeypatch):
        adapter = self._make_adapter()
        captured_kwargs = {}

        def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            tool_block = SimpleNamespace(type="tool_use", input={"reasoning": "ok", "propositions": []})
            return SimpleNamespace(content=[tool_block], usage=SimpleNamespace(input_tokens=1, output_tokens=1))

        monkeypatch.setattr(adapter._client.messages, "create", fake_create)

        adapter.complete(
            [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=42,
            response_schema=PropositionSegmenterOutput,
        )

        assert "seed" not in captured_kwargs


class TestOllamaAdapter:
    def _make_adapter(self):
        from llm.adapters.ollama_adapter import OllamaAdapter

        return OllamaAdapter(base_url="http://localhost:11434", seed=42)

    def test_complete_posts_format_schema_and_parses_content(self, monkeypatch):
        adapter = self._make_adapter()
        response_body = {
            "message": {
                "content": '{"reasoning": "ok", "propositions": [{"text": "Acme Corp net sales were $32.2 billion.", "source_offset_hint": null}]}'
            },
            "prompt_eval_count": 50,
            "eval_count": 20,
        }
        fake_response = SimpleNamespace(
            json=lambda: response_body, raise_for_status=lambda: None
        )

        captured = {}

        def fake_post(url, json):
            captured["url"] = url
            captured["json"] = json
            return fake_response

        monkeypatch.setattr(adapter._client, "post", fake_post)

        result = adapter.complete(
            [{"role": "user", "content": "x"}], model="qwen2.5:7b-instruct-q4_K_M",
            temperature=0.0, seed=None, response_schema=PropositionSegmenterOutput,
        )

        assert result.data["propositions"][0]["text"] == "Acme Corp net sales were $32.2 billion."
        assert result.input_tokens == 50
        assert result.output_tokens == 20
        assert result.seed_supported is True  # falls back to the adapter's default seed (42)
        assert captured["url"] == "/api/chat"
        assert "format" in captured["json"]

    def test_seed_supported_false_when_no_seed_configured_anywhere(self, monkeypatch):
        from llm.adapters.ollama_adapter import OllamaAdapter

        adapter = OllamaAdapter(base_url="http://localhost:11434", seed=None)
        response_body = {
            "message": {"content": '{"reasoning": "ok", "propositions": []}'},
            "prompt_eval_count": 5, "eval_count": 5,
        }
        fake_response = SimpleNamespace(json=lambda: response_body, raise_for_status=lambda: None)
        monkeypatch.setattr(adapter._client, "post", lambda url, json: fake_response)

        result = adapter.complete(
            [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
            response_schema=PropositionSegmenterOutput,
        )

        assert result.seed_supported is False

    def test_malformed_content_raises_llm_call_failed_with_partial_usage(self, monkeypatch):
        adapter = self._make_adapter()
        response_body = {
            "message": {"content": "not valid json at all"},
            "prompt_eval_count": 15, "eval_count": 8,
        }
        fake_response = SimpleNamespace(json=lambda: response_body, raise_for_status=lambda: None)
        monkeypatch.setattr(adapter._client, "post", lambda url, json: fake_response)

        with pytest.raises(LLMCallFailed) as exc_info:
            adapter.complete(
                [{"role": "user", "content": "x"}], model="m", temperature=0.0, seed=None,
                response_schema=PropositionSegmenterOutput,
            )

        assert exc_info.value.partial_response.input_tokens == 15
        assert exc_info.value.partial_response.output_tokens == 8
