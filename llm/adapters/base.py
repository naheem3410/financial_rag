"""Provider adapter interface.

Every adapter normalizes one provider's structured-output mechanism (OpenAI
`response_format`, Anthropic tool-use, Ollama `format`) behind one method,
`complete`. Call-site code — via `llm/routing.py`'s `RoutedLLMClient`, which
is what `LLMClient` (`llm/client.py`) actually resolves to in a real run —
always gets back a validated dict, regardless of provider. `tests/
test_provider_swap.py` checks that abstraction actually holds.

`AdapterResponse` carries what the manifest and cost accounting need,
normalized across providers that don't all report the same things:
- `seed_supported`: only a local/self-hosted provider (Ollama) can actually
  guarantee reproducibility from a seed; hosted APIs (OpenAI, Anthropic)
  get `False` even if a seed parameter is accepted, since it's best-effort
  only there — never pretend otherwise.
- `reasoning_tokens`: some models bill hidden reasoning tokens as output;
  reported separately so cost accounting isn't silently wrong.
- `temperature_supported`: reasoning-family models reject any `temperature`
  other than their fixed default (OpenAI returns `400 unsupported_value`
  for `temperature=0`), so a requested `temperature=0` isn't a guarantee
  every provider/model actually honors it, same as `seed_supported` above.
  Defaults to `True`; `OpenAIAdapter` sets it `False` only on the specific
  fallback path where it retried at the provider's mandated temperature
  instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Type

from pydantic import BaseModel


class LLMCallFailed(Exception):
    """Raised by a `ProviderAdapter` when a call couldn't produce valid
    structured output for a reason outside caller control — hitting the
    provider's output-length limit before finishing, a provider refusal, a
    malformed tool-use response, and similar. Callers (e.g.
    `offline/propositions/segmenter_llm.py:segment_propositions`'s
    retry/give-up loop) treat this like "the model returned nothing usable"
    — retry, then drop, never let one bad response crash a whole document
    or run.

    `partial_response` carries whatever token-usage accounting the provider
    still reports even on failure (a length-limit failure can burn tens of
    thousands of completion tokens before giving up) — real cost, so cost
    tracking (`offline/pilot.py`) shouldn't silently ignore it just because
    the call ultimately failed. `None` when the provider gives no usage
    info for the failure at all.
    """

    def __init__(self, message: str, partial_response: Optional["AdapterResponse"] = None) -> None:
        super().__init__(message)
        self.partial_response = partial_response


class LLMInfrastructureError(Exception):
    """Raised by a `ProviderAdapter` for a failure that says nothing about
    the *content* being segmented — quota exhaustion, authentication or
    permission failures, a connection error, or rate limiting that persists
    after backoff.

    Deliberately not an `LLMCallFailed` subclass — the retry/drop/circuit-
    breaker handlers in `offline/propositions/segmenter_llm.py` and
    `offline/pilot.py` must not catch this the same way. The two failure
    modes are opposite in kind: `LLMCallFailed` means "this specific span
    didn't segment, the account and connection are fine, move on";
    `LLMInfrastructureError` means "nothing this run produces from here on
    can be trusted, stop." A page that's genuinely hard to segment and an
    account that's out of credit must never look the same to the caller —
    treating the second like the first would silently produce an empty
    proposition set for every remaining page, tripping the circuit
    breaker's failure threshold along the way and leaving a partial index
    that looks complete.
    """


@dataclass(frozen=True)
class AdapterResponse:
    data: dict[str, Any]
    seed_supported: bool
    reasoning_tokens: Optional[int]
    input_tokens: int
    output_tokens: int
    adapter_version: str
    temperature_supported: bool = True


class ProviderAdapter(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        seed: Optional[int],
        response_schema: Type[BaseModel],
    ) -> AdapterResponse: ...
