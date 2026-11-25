"""OpenAI provider adapter, via `chat.completions.parse`. Content failures
raise `LLMCallFailed`, infra failures raise `LLMInfrastructureError` (base.py).
"""
from __future__ import annotations

import time
from typing import Optional, Type

from openai import (  # SDK import confined to llm/; re-exported below for tests
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel

from llm.adapters.base import AdapterResponse, LLMCallFailed, LLMInfrastructureError

_QUOTA_EXHAUSTED_CODE = "insufficient_quota"
# reasoning models reject temperature != their fixed default via this
# BadRequestError param; fallback below is their mandated value, not a guess
_UNSUPPORTED_TEMPERATURE_PARAM = "temperature"
_TEMPERATURE_FALLBACK = 1.0


class OpenAIAdapter:
    ADAPTER_VERSION = "1.0.0"

    def __init__(
        self,
        api_key: str,
        rate_limit_max_retries: int = 3,
        rate_limit_backoff_base_seconds: float = 1.0,
    ) -> None:
        self._client = OpenAI(api_key=api_key)
        self._rate_limit_max_retries = rate_limit_max_retries
        self._rate_limit_backoff_base_seconds = rate_limit_backoff_base_seconds

    def _reasoning_tokens(self, usage) -> Optional[int]:
        details = getattr(usage, "completion_tokens_details", None)
        return getattr(details, "reasoning_tokens", None) if details is not None else None

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        seed: Optional[int],
        response_schema: Type[BaseModel],
    ) -> AdapterResponse:
        response, temperature_supported = self._complete_with_backoff(
            messages, model=model, temperature=temperature, seed=seed, response_schema=response_schema
        )
        choice = response.choices[0]
        parsed = choice.message.parsed
        if parsed is None:
            refusal = getattr(choice.message, "refusal", None)
            raise LLMCallFailed(
                f"OpenAIAdapter: model {model!r} returned no parseable structured "
                f"output" + (f" (refusal: {refusal})" if refusal else "")
            )

        usage = response.usage
        return AdapterResponse(
            data=parsed.model_dump(),
            seed_supported=False,  # best-effort only, never guaranteed by OpenAI
            reasoning_tokens=self._reasoning_tokens(usage),
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            adapter_version=self.ADAPTER_VERSION,
            temperature_supported=temperature_supported,
        )

    def _complete_with_backoff(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        seed: Optional[int],
        response_schema: Type[BaseModel],
        _temperature_already_overridden: bool = False,
    ):
        attempt = 0
        while True:
            try:
                response = self._client.chat.completions.parse(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    seed=seed,
                    response_format=response_schema,
                )
                return response, not _temperature_already_overridden
            except BadRequestError as exc:
                if (
                    not _temperature_already_overridden
                    and getattr(exc, "param", None) == _UNSUPPORTED_TEMPERATURE_PARAM
                ):
                    # reasoning model -- retry once at the provider's mandated
                    # temperature instead of failing the call outright
                    return self._complete_with_backoff(
                        messages, model=model, temperature=_TEMPERATURE_FALLBACK, seed=seed,
                        response_schema=response_schema, _temperature_already_overridden=True,
                    )
                raise LLMCallFailed(f"OpenAIAdapter: model {model!r} call failed: {exc}") from exc
            except LengthFinishReasonError as exc:
                # length limit hit -- real tokens spent, cost accounting still
                # needs them even with no usable output; content, not infra
                usage = exc.completion.usage
                partial = AdapterResponse(
                    data={},
                    seed_supported=False,
                    reasoning_tokens=self._reasoning_tokens(usage),
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    adapter_version=self.ADAPTER_VERSION,
                )
                raise LLMCallFailed(
                    f"OpenAIAdapter: model {model!r} hit the completion length limit "
                    f"({usage.completion_tokens} completion tokens) before finishing "
                    "structured output",
                    partial_response=partial,
                ) from exc
            except (AuthenticationError, PermissionDeniedError, APIConnectionError) as exc:
                # says nothing about content -- nothing further can be trusted
                raise LLMInfrastructureError(
                    f"OpenAIAdapter: model {model!r} call failed with an infrastructure "
                    f"error ({type(exc).__name__}): {exc}"
                ) from exc
            except RateLimitError as exc:
                if exc.code == _QUOTA_EXHAUSTED_CODE:
                    raise LLMInfrastructureError(
                        f"OpenAIAdapter: model {model!r} call failed — the account is out "
                        f"of quota ({exc.code!r}). Stopping the run rather than silently "
                        "producing empty propositions for every remaining page: "
                        f"{exc}"
                    ) from exc
                if attempt >= self._rate_limit_max_retries:
                    raise LLMInfrastructureError(
                        f"OpenAIAdapter: model {model!r} still rate-limited after "
                        f"{attempt + 1} attempts with backoff — treating as a systemic "
                        f"issue, not a per-call content problem: {exc}"
                    ) from exc
                time.sleep(self._rate_limit_backoff_base_seconds * (2**attempt))
                attempt += 1
            except OpenAIError as exc:
                # content filter, non-temperature bad request, etc -- no usage
                # info available here, so partial_response stays None
                raise LLMCallFailed(f"OpenAIAdapter: model {model!r} call failed: {exc}") from exc
