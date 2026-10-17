"""Shared fixtures for instrumenting the LLM client boundary (llm/client.py).

One patch point for "zero LLM calls" assertions, so a hand-rolled monkeypatch
mistake in some test can't quietly pass for the wrong reason.
"""
from __future__ import annotations

from typing import Any

import pytest

from llm.client import LLMClient


class _LLMRecorder:
    """Records every payload that crosses `LLMClient.complete`."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    @property
    def count(self) -> int:
        return len(self.payloads)


@pytest.fixture
def _llm_recorder(monkeypatch: pytest.MonkeyPatch) -> _LLMRecorder:
    recorder = _LLMRecorder()

    def _recording_complete(self: LLMClient, payload: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        recorder.payloads.append(payload)
        # no provider wired yet, empty stub lets callers proceed
        return {}

    monkeypatch.setattr(LLMClient, "complete", _recording_complete)
    return recorder


@pytest.fixture
def llm_call_counter(_llm_recorder: _LLMRecorder) -> _LLMRecorder:
    """Exposes `.count` — the number of calls that crossed the LLM boundary."""
    return _llm_recorder


@pytest.fixture
def payload_capture(_llm_recorder: _LLMRecorder) -> list[dict[str, Any]]:
    """The exact payload of each call, in call order."""
    return _llm_recorder.payloads


# Dev-only creds for a local postgres:16.4 container, not a real secret.
# clean_postgres truncates whatever DB its DSN points at -- must never be prod.
PRODUCTION_POSTGRES_DB_NAME = "financial_rag"
TEST_POSTGRES_DB_NAME = "financial_rag_test"
assert TEST_POSTGRES_DB_NAME != PRODUCTION_POSTGRES_DB_NAME, (
    "tests/conftest.py: TEST_POSTGRES_DB_NAME must never equal "
    "PRODUCTION_POSTGRES_DB_NAME -- clean_postgres truncates whatever database its DSN "
    "names, and pointing it at production would silently wipe a real index build. Fails "
    "at collection time, deliberately, so this can never be edited back to production "
    "and only discovered after the fact."
)
POSTGRES_TEST_DSN = f"postgresql://financial_rag:financial_rag_dev@localhost:5432/{TEST_POSTGRES_DB_NAME}"


@pytest.fixture
def clean_postgres() -> str:
    """Truncates the propositions table before and after the test, in the
    dedicated test database (never production — see the assertion above)."""
    from offline.indexing.metadata_store_postgres import PostgresMetadataStore

    store = PostgresMetadataStore(POSTGRES_TEST_DSN)
    store.clear()
    try:
        yield POSTGRES_TEST_DSN
    finally:
        store.clear()
        store.close()
