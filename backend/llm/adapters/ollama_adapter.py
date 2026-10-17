"""Ollama provider adapter — local/self-hosted.

Only provider that gets a genuine `seed_supported=True`; fixed weights and
hardware make Ollama's seed actually reproducible, unlike the hosted APIs.
"""
from __future__ import annotations

from typing import Optional, Type

from pydantic import BaseModel, ValidationError

from llm.adapters.base import AdapterResponse, LLMCallFailed


class OllamaAdapter:
    ADAPTER_VERSION = "1.0.0"

    def __init__(self, base_url: str, seed: Optional[int] = None) -> None:
        import httpx  # SDK import confined to llm/

        self._client = httpx.Client(base_url=base_url, timeout=120.0)
        self._default_seed = seed

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        seed: Optional[int],
        response_schema: Type[BaseModel],
    ) -> AdapterResponse:
        effective_seed = seed if seed is not None else self._default_seed

        response = self._client.post(
            "/api/chat",
            json={
                "model": model,
                "messages": messages,
                "format": response_schema.model_json_schema(),
                "options": {"temperature": temperature, "seed": effective_seed},
                "stream": False,
            },
        )
        response.raise_for_status()
        body = response.json()

        try:
            parsed = response_schema.model_validate_json(body["message"]["content"])
        except ValidationError as exc:
            partial = AdapterResponse(
                data={},
                seed_supported=effective_seed is not None,
                reasoning_tokens=None,
                input_tokens=body.get("prompt_eval_count", 0),
                output_tokens=body.get("eval_count", 0),
                adapter_version=self.ADAPTER_VERSION,
            )
            raise LLMCallFailed(
                f"OllamaAdapter: model {model!r} returned content that does not "
                "match the requested schema",
                partial_response=partial,
            ) from exc

        return AdapterResponse(
            data=parsed.model_dump(),
            seed_supported=effective_seed is not None,
            reasoning_tokens=None,
            input_tokens=body.get("prompt_eval_count", 0),
            output_tokens=body.get("eval_count", 0),
            adapter_version=self.ADAPTER_VERSION,
        )
