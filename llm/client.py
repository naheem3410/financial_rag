"""The LLM client boundary.

Every LLM call has to go through `LLMClient.complete`, and this is the only
module outside `llm/` allowed to import a provider SDK (`anthropic`,
`openai`, `google.generativeai`, `litellm`, ...) — enforced by
`tests/test_no_direct_sdk_imports.py`. This class itself is just the seam:
tests patch this exact method (`llm_call_counter`, `payload_capture` in
`tests/conftest.py`) so "zero LLM calls" and "draft absent from payload"
assertions observe real call sites rather than a test-controlled mock.

`complete` is synchronous on purpose. The online query plane runs one fixed
sequential path per request, so no call site needs two LLM calls in flight
at once. Concurrency belongs at the request level (parallel processes or
threads, each driving its own synchronous client), not inside one request's
call chain. If a provider client is async, wrap it with `asyncio.run(...)`
inside `complete` rather than making `complete` itself async — otherwise
`monkeypatch.setattr` on a sync method stops intercepting the call the
moment a real async provider lands.
"""
from __future__ import annotations

from typing import Any


class LLMClient:
    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            "LLMClient has no provider wired. Use llm.routing.build_llm_client() "
            "to get a real, configured client instead."
        )
