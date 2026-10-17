"""The LLM client boundary — every call goes through `LLMClient.complete`.
Stays sync; wrap an async provider inside complete, don't make complete itself async.
"""
from __future__ import annotations

from typing import Any


class LLMClient:
    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            "LLMClient has no provider wired. Use llm.routing.build_llm_client() "
            "to get a real, configured client instead."
        )
