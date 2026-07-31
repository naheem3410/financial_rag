"""Per-call-site LLM routing.

`build_llm_client(call_site_name, config)` builds a `RoutedLLMClient` — a
concrete `LLMClient` (same `.complete(payload) -> dict` shape as
`llm/client.py`) bound to that call site's configured provider, model,
temperature, seed, and structured-output schema. Call-site code (e.g.
`offline/propositions/segmenter_llm.py`) never has to change when a call
site's provider or model changes in config — that's the whole point of this
module, and `tests/test_provider_swap.py` checks it actually holds.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Optional, Type

from pydantic import BaseModel

from config.schema import ExperimentConfig, ProviderConnectionConfig
from llm.adapters.base import AdapterResponse, ProviderAdapter
from llm.rate_limiter import RateLimiter
from llm.schemas import (
    ClaimExtractionOutput,
    FFFRewriterOutput,
    GeneratorOutput,
    IndependentVerificationOutput,
    NaiveGeneratorOutput,
    NumericQualifierResolution,
    ProseComparisonOutput,
    PropositionSegmenterOutput,
    PropositionSegmenterOutputNoReasoning,
    QuerySplitterOutput,
)

# schema key -> (schema with `reasoning`, schema without it, or None if no
# no-reasoning variant is defined). Keyed by *schema key*, which for most
# call sites is just the call_site_name -- except CoVe, which is one config
# entry (`cove_verification`, one model choice) but makes three LLM
# sub-calls with three different output shapes (claim extraction /
# independent execution / prose comparison). That's why `build_llm_client`
# takes a separate `schema_key` parameter below, distinct from the config
# lookup key. `naive_generator` reuses the `generator` config slot the same
# way -- see online/output/naive_generator.py for why.
_SCHEMAS: dict[str, tuple[Type[BaseModel], Optional[Type[BaseModel]]]] = {
    "proposition_segmenter": (PropositionSegmenterOutput, PropositionSegmenterOutputNoReasoning),
    "numeric_qualifier_resolver": (NumericQualifierResolution, None),
    "query_splitter": (QuerySplitterOutput, None),
    "fff_rewriter": (FFFRewriterOutput, None),
    "generator": (GeneratorOutput, None),
    "naive_generator": (NaiveGeneratorOutput, None),
    "cove_claim_extraction": (ClaimExtractionOutput, None),
    "cove_independent_verification": (IndependentVerificationOutput, None),
    "cove_prose_comparison": (ProseComparisonOutput, None),
}


def _resolve_schema(call_site_name: str, include_reasoning_field: bool) -> Type[BaseModel]:
    if call_site_name not in _SCHEMAS:
        raise ValueError(
            f"llm.routing: no output schema registered for call site "
            f"{call_site_name!r} yet — add one to llm/routing.py:_SCHEMAS "
            "before routing this call site to a real provider."
        )
    with_reasoning, without_reasoning = _SCHEMAS[call_site_name]
    if include_reasoning_field or without_reasoning is None:
        return with_reasoning
    return without_reasoning


def _estimate_tokens(messages: list[dict[str, str]], model: str) -> int:
    """Rough pre-call token estimate, used only to gate the TPM rate limit
    (`RateLimiter.acquire(estimated_tokens=...)`) before a call is made —
    actual usage (known only after the response) still drives cost
    accounting (`AdapterResponse.input_tokens`/`output_tokens`). An
    estimate is unavoidable here: TPM limiting exists specifically to avoid
    making the call whose real token count would only be known after
    making it.
    """
    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        return sum(len(encoding.encode(m["content"])) for m in messages) + 8 * len(messages)
    except Exception:
        return sum(len(m["content"]) for m in messages) // 4  # ~4 chars/token, English text


def _build_adapter(provider_name: str, provider_config: ProviderConnectionConfig) -> ProviderAdapter:
    if provider_name == "openai":
        from llm.adapters.openai_adapter import OpenAIAdapter

        api_key = os.environ[provider_config.api_key_env] if provider_config.api_key_env else None
        return OpenAIAdapter(api_key=api_key)
    if provider_name == "anthropic":
        from llm.adapters.anthropic_adapter import AnthropicAdapter

        api_key = os.environ[provider_config.api_key_env] if provider_config.api_key_env else None
        return AnthropicAdapter(api_key=api_key)
    if provider_name == "ollama":
        from llm.adapters.ollama_adapter import OllamaAdapter

        return OllamaAdapter(base_url=provider_config.base_url or "http://localhost:11434", seed=provider_config.seed)
    raise ValueError(f"llm.routing: unknown provider {provider_name!r}")


@dataclass(frozen=True)
class LLMCallSiteManifest:
    """One row per call site actually exercised in a run. With multi-provider
    routing, "pinned model version" isn't a single global setting anymore --
    a run using two providers has to be fully reconstructable from these
    rows alone."""

    call_site: str
    provider: str
    model: str
    temperature: float
    seed: Optional[int]
    seed_supported: bool
    include_reasoning_field: bool
    adapter_version: str
    temperature_supported: bool = True


class RoutedLLMClient:
    """Concrete `LLMClient`. `last_response_meta` exposes token/cost
    telemetry (`AdapterResponse`) for callers that need it — the pilot
    runner (`offline/pilot.py`) — without changing the `.complete()`
    contract every call site relies on. `to_manifest` reads that telemetry
    back out as an `LLMCallSiteManifest` row.

    Thread safety: a single `RoutedLLMClient` can be shared across worker
    threads (`offline/propositions/segmenter_llm.py`'s `max_workers>1`
    path). `last_response_meta` is thread-local: `pilot.py`'s
    `_tracked_complete` reads it right after each `.complete()` call to
    attribute that call's tokens for cost accounting, and a single shared
    mutable attribute would let one thread's read race another thread's
    write, silently mis-attributing (or losing) real spend. `seed_supported`
    and `adapter_version` don't vary across calls to the same client, so
    those two are tracked separately, under a lock, from whichever thread
    happens to complete a call.
    """

    def __init__(
        self,
        adapter: ProviderAdapter,
        provider_name: str,
        model: str,
        temperature: float,
        seed: Optional[int],
        response_schema: Type[BaseModel],
        rate_limiter: Optional[RateLimiter] = None,
        include_reasoning_field: bool = True,
    ) -> None:
        self._adapter = adapter
        self._provider_name = provider_name
        self._model = model
        self._temperature = temperature
        self._seed = seed
        self._response_schema = response_schema
        self._rate_limiter = rate_limiter
        self._include_reasoning_field = include_reasoning_field
        self._thread_local = threading.local()
        self._manifest_lock = threading.Lock()
        self._last_seed_supported: Optional[bool] = None
        self._last_adapter_version: Optional[str] = None
        self._last_temperature_supported: Optional[bool] = None

    @property
    def last_response_meta(self) -> Optional[AdapterResponse]:
        """The calling thread's own most recent response — see class
        docstring. `None` if the calling thread has not itself completed a
        call yet, even if other threads have."""
        return getattr(self._thread_local, "last_response_meta", None)

    def current_tpm_usage(self) -> Optional[int]:
        """Current tokens-per-minute usage from the shared rate limiter, for
        periodic observability logging during a real run (2026-07-25).
        `None` if this client has no rate limiter configured."""
        if self._rate_limiter is None:
            return None
        return self._rate_limiter.current_tpm_usage()

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        system_prompt = payload.get("system_prompt", "")
        user_payload = {k: v for k, v in payload.items() if k != "system_prompt"}
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload)},
        ]

        if self._rate_limiter is not None:
            self._rate_limiter.acquire(estimated_tokens=_estimate_tokens(messages, self._model))

        response = self._adapter.complete(
            messages,
            model=self._model,
            temperature=self._temperature,
            seed=self._seed,
            response_schema=self._response_schema,
        )
        self._thread_local.last_response_meta = response
        with self._manifest_lock:
            self._last_seed_supported = response.seed_supported
            self._last_adapter_version = response.adapter_version
            self._last_temperature_supported = response.temperature_supported
        return response.data

    def to_manifest(self, call_site_name: str) -> LLMCallSiteManifest:
        with self._manifest_lock:
            seed_supported = self._last_seed_supported
            adapter_version = self._last_adapter_version
            temperature_supported = self._last_temperature_supported
        if adapter_version is None:
            raise RuntimeError(
                f"RoutedLLMClient for {call_site_name!r} has not completed a call yet — "
                "seed_supported/adapter_version are only known after a real response."
            )
        return LLMCallSiteManifest(
            call_site=call_site_name,
            provider=self._provider_name,
            model=self._model,
            temperature=self._temperature,
            seed=self._seed,
            seed_supported=seed_supported,
            include_reasoning_field=self._include_reasoning_field,
            adapter_version=adapter_version,
            temperature_supported=temperature_supported if temperature_supported is not None else True,
        )


def build_llm_client(
    call_site_name: str, config: ExperimentConfig, schema_key: Optional[str] = None
) -> RoutedLLMClient:
    """`schema_key` defaults to `call_site_name` (every call site except CoVe
    has one config entry mapping to one schema). CoVe's three sub-calls
    share a single config entry (`cove_verification`) but need three
    different output schemas, so callers building those clients pass
    `schema_key="cove_claim_extraction"` / `"cove_independent_verification"` /
    `"cove_prose_comparison"` explicitly (see
    online/verification/cove_orchestrator.py)."""
    call_site_config = config.llm.call_sites[call_site_name]
    provider_config = config.llm.providers[call_site_config.provider]

    adapter = _build_adapter(call_site_config.provider, provider_config)
    schema = _resolve_schema(schema_key or call_site_name, call_site_config.include_reasoning_field)
    rate_limiter = RateLimiter(rpm=provider_config.rpm, tpm=provider_config.tpm, rpd=provider_config.rpd)
    seed = call_site_config.seed if call_site_config.seed is not None else provider_config.seed

    return RoutedLLMClient(
        adapter=adapter,
        provider_name=call_site_config.provider,
        model=call_site_config.model,
        temperature=call_site_config.temperature,
        seed=seed,
        response_schema=schema,
        rate_limiter=rate_limiter,
        include_reasoning_field=call_site_config.include_reasoning_field,
    )
