"""Same call site through two different provider adapters must return the same schema.
SDK calls are monkeypatched to provider-shaped fakes, no real network calls.
"""
from __future__ import annotations

from types import SimpleNamespace

from llm.adapters.anthropic_adapter import AnthropicAdapter
from llm.adapters.openai_adapter import OpenAIAdapter
from llm.rate_limiter import RateLimiter
from llm.routing import RoutedLLMClient
from llm.schemas import PropositionSegmenterOutput

_SAME_LOGICAL_ANSWER = {
    "reasoning": "Segmented the source span into one proposition.",
    "propositions": [{"text": "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", "source_offset_hint": None}],
}


def _openai_client_returning(monkeypatch, data: dict) -> RoutedLLMClient:
    adapter = OpenAIAdapter(api_key="test-key-not-real")
    parsed = PropositionSegmenterOutput.model_validate(data)
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, completion_tokens_details=None),
    )
    monkeypatch.setattr(adapter._client.chat.completions, "parse", lambda **kwargs: fake_response)
    return RoutedLLMClient(adapter=adapter, provider_name="openai", model="gpt-4.1-mini-2025-04-14", temperature=0.0, seed=42, response_schema=PropositionSegmenterOutput)


def _anthropic_client_returning(monkeypatch, data: dict) -> RoutedLLMClient:
    adapter = AnthropicAdapter(api_key="test-key-not-real")
    tool_block = SimpleNamespace(type="tool_use", input=data)
    fake_response = SimpleNamespace(content=[tool_block], usage=SimpleNamespace(input_tokens=80, output_tokens=40))
    monkeypatch.setattr(adapter._client.messages, "create", lambda **kwargs: fake_response)
    return RoutedLLMClient(adapter=adapter, provider_name="anthropic", model="claude-x-2025-01-01", temperature=0.0, seed=42, response_schema=PropositionSegmenterOutput)


def test_provider_swap_same_call_site_same_schema_both_providers(monkeypatch):
    payload = {
        "system_prompt": "You split financial text into propositions.",
        "source_span": "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.",
    }

    openai_client = _openai_client_returning(monkeypatch, _SAME_LOGICAL_ANSWER)
    anthropic_client = _anthropic_client_returning(monkeypatch, _SAME_LOGICAL_ANSWER)

    openai_result = openai_client.complete(payload)
    anthropic_result = anthropic_client.complete(payload)

    PropositionSegmenterOutput.model_validate(openai_result)
    PropositionSegmenterOutput.model_validate(anthropic_result)
    # identical logical content should be identical as data, call site can't tell providers apart
    assert openai_result == anthropic_result


def test_provider_swap_call_site_code_is_unaware_of_which_provider_answered():
    """Call-site contract is .complete(payload) -> dict regardless of what's behind it."""

    def call_site_logic(llm_client) -> list[str]:
        response = llm_client.complete({"system_prompt": "sys", "source_span": "text"})
        return [p["text"] for p in response["propositions"]]

    class _StubAdapter:
        def complete(self, messages, *, model, temperature, seed, response_schema):
            from llm.adapters.base import AdapterResponse

            return AdapterResponse(
                data=_SAME_LOGICAL_ANSWER, seed_supported=False, reasoning_tokens=None,
                input_tokens=1, output_tokens=1, adapter_version="stub-1.0",
            )

    client_a = RoutedLLMClient(adapter=_StubAdapter(), provider_name="stub", model="m1", temperature=0.0, seed=None, response_schema=PropositionSegmenterOutput)
    client_b = RoutedLLMClient(adapter=_StubAdapter(), provider_name="stub", model="m2", temperature=0.0, seed=None, response_schema=PropositionSegmenterOutput)

    assert call_site_logic(client_a) == call_site_logic(client_b)


def test_provider_swap_rate_limiter_is_provider_agnostic(monkeypatch):
    """Swapping the adapter underneath must not change limiter behavior."""
    limiter = RateLimiter(rpm=1000)

    openai_client = _openai_client_returning(monkeypatch, _SAME_LOGICAL_ANSWER)
    openai_client._rate_limiter = limiter
    anthropic_client = _anthropic_client_returning(monkeypatch, _SAME_LOGICAL_ANSWER)
    anthropic_client._rate_limiter = limiter

    openai_client.complete({"system_prompt": "sys"})
    anthropic_client.complete({"system_prompt": "sys"})

    assert len(limiter._request_times) == 2
