"""OpenAI provider adapter.

One of the modules the SDK-import scan (`tests/test_no_direct_sdk_imports.py`)
exempts — `llm/` is the one permitted seam. Uses `chat.completions.parse`
(structured output via a pydantic model passed directly as
`response_format`) so the adapter never hand-writes a JSON Schema payload.

`LengthFinishReasonError`/`ContentFilterFinishReasonError`/`OpenAIError`/
`AuthenticationError`/`PermissionDeniedError`/`APIConnectionError`/
`RateLimitError` are re-exported from this module rather than imported
directly from `openai`, so tests can construct realistic failures without
importing the SDK themselves — `tests/` is deliberately not exempt from the
SDK-import scan, so a test needing a real exception type imports it from
here instead.

Error handling here falls into two buckets. Content failures — the model
hit its completion-length limit, tripped a content filter, or returned an
otherwise malformed response — are caught via the SDK's common `OpenAIError`
base and raised as `LLMCallFailed`, so callers retry then drop rather than
crash. Only `LengthFinishReasonError` reliably carries token-usage info on
failure (`exc.completion.usage`); other subclasses don't, so
`partial_response` is `None` for those.

Infrastructure failures — quota exhaustion, auth/permission errors,
connection errors, or rate limiting that persists after backoff — say
nothing about the content and mean nothing further in the run can be
trusted. Those raise `LLMInfrastructureError` instead, deliberately not an
`LLMCallFailed` subclass (see that class's docstring in
`llm/adapters/base.py` for why it needs to propagate uncaught through the
retry/drop/circuit-breaker chain and stop the run). Ordinary rate limiting
(too many requests, not quota) gets a few retries with exponential backoff
first, since the proactive rate limiter can still occasionally race a real
request past the provider's own limit — a single 429 there isn't evidence
of a dead account.
"""
from __future__ import annotations

import time
from typing import Optional, Type

from openai import (  # confined to llm/, see module docstring
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
# Reasoning-family models reject any `temperature` other than their fixed
# default and report it via this specific BadRequestError shape, distinct
# from the other OpenAIError failures this adapter treats as content
# failures. The fallback value below is the provider's mandated value, not
# a guess.
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
                    # Reasoning-family model -- retry once at the provider's
                    # mandated temperature instead of failing the call. Same
                    # idea as seed_supported: a per-model capability gap,
                    # not a guess on our part.
                    return self._complete_with_backoff(
                        messages, model=model, temperature=_TEMPERATURE_FALLBACK, seed=seed,
                        response_schema=response_schema, _temperature_already_overridden=True,
                    )
                raise LLMCallFailed(f"OpenAIAdapter: model {model!r} call failed: {exc}") from exc
            except LengthFinishReasonError as exc:
                # Hit the output-length limit before finishing valid
                # structured output -- real tokens were spent, so cost
                # accounting still needs to see them even though there's no
                # usable data. A content failure, not infrastructure.
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
                # Auth/permission failures and connection errors say nothing
                # about the content -- nothing further in the run can be
                # trusted.
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
                # Any other OpenAI-side failure (content filter, non-
                # temperature bad request, ...). No completion/usage info is
                # reliably available for these, so partial_response is None;
                # the caller's retry/give-up logic still applies.
                raise LLMCallFailed(f"OpenAIAdapter: model {model!r} call failed: {exc}") from exc
