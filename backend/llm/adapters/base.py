"""Provider adapter interface — one `complete` method every adapter normalizes
behind (OpenAI response_format, Anthropic tool-use, Ollama format).
`AdapterResponse` carries seed/temperature support flags since providers
report these differently; see field comments below.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Type

from pydantic import BaseModel


class LLMCallFailed(Exception):
    """A call didn't produce usable structured output (length limit, refusal,
    bad tool-use response). Callers retry then drop, never crash the run.

    `partial_response` carries usage the provider still reported on failure
    (real cost, e.g. a length-limit failure can burn many tokens), or `None`.
    """

    def __init__(self, message: str, partial_response: Optional["AdapterResponse"] = None) -> None:
        super().__init__(message)
        self.partial_response = partial_response


class LLMInfrastructureError(Exception):
    """Quota/auth/connection/persistent-rate-limit failures — says nothing
    about content. Deliberately not an `LLMCallFailed` subclass: this means
    stop the run, not retry-and-move-on like a per-span failure.
    """


@dataclass(frozen=True)
class AdapterResponse:
    data: dict[str, Any]
    seed_supported: bool  # True only for local providers (Ollama); hosted APIs are best-effort at most
    reasoning_tokens: Optional[int]  # hidden reasoning tokens billed as output, when a model reports them
    input_tokens: int
    output_tokens: int
    adapter_version: str
    temperature_supported: bool = True  # False if the provider forced a different temperature than requested


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
