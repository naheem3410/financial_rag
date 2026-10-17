"""Anthropic provider adapter.

Untested against a live endpoint (no ANTHROPIC_API_KEY configured). Do a real
smoke call before routing a paid call site through this.
"""
from __future__ import annotations

from typing import Optional, Type

from pydantic import BaseModel

from llm.adapters.base import AdapterResponse, LLMCallFailed

_TOOL_NAME = "emit_structured_output"
_MAX_TOKENS = 4096


class AnthropicAdapter:
    ADAPTER_VERSION = "1.0.0"

    def __init__(self, api_key: str) -> None:
        from anthropic import Anthropic  # SDK import confined to llm/

        self._client = Anthropic(api_key=api_key)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        seed: Optional[int],
        response_schema: Type[BaseModel],
    ) -> AdapterResponse:
        system_messages = [m["content"] for m in messages if m["role"] == "system"]
        user_messages = [m for m in messages if m["role"] != "system"]

        tool = {
            "name": _TOOL_NAME,
            "description": "Emit the structured output for this task.",
            "input_schema": response_schema.model_json_schema(),
        }

        response = self._client.messages.create(
            model=model,
            system="\n\n".join(system_messages) if system_messages else "",
            messages=user_messages,
            temperature=temperature,
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            max_tokens=_MAX_TOKENS,
        )

        tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_use_block is None:
            raise LLMCallFailed(f"AnthropicAdapter: model {model!r} did not return a tool_use block")

        parsed = response_schema.model_validate(tool_use_block.input)

        usage = response.usage
        return AdapterResponse(
            data=parsed.model_dump(),
            seed_supported=False,  # Messages API has no seed param
            reasoning_tokens=None,  # not broken out in Anthropic's usage object
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            adapter_version=self.ADAPTER_VERSION,
        )
