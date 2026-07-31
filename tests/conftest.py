"""Fixtures instrumenting the LLM client boundary (llm/client.py).

Several load-bearing acceptance tests assert either "zero LLM calls" or
"field absent from payload". Those assertions are only as trustworthy as
the mechanism that observes calls. If each test hand-rolled its own
`monkeypatch.setattr(LLMClient, "complete", ...)`, a wiring mistake in any
one of them would make that test pass regardless of what the component
under test actually does — green for the wrong reason.

These fixtures patch `LLMClient.complete` itself — the one seam every call
site must cross — in a single place, verified once by the meta-tests in
`test_llm_instrumentation.py`. Tests then depend on the fixture, not on
their own copy of the patching logic.
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
        # No provider is wired yet (llm/client.py); return an empty stub so
        # instrumented code under test can proceed past the call.
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


# Project-scoped postgres:16.4 Docker container backing the persistent
# metadata store. Dev-only credentials for a local container, not a real
# secret.
#
# Database isolation matters here: `clean_postgres` truncates whatever
# database its DSN points at, on every test that uses it. Pointing that DSN
# at the production `financial_rag` database -- the same one
# `offline/full_build.py`'s real index build persists to -- would let the
# test suite silently wipe it, since all five experiment cells need to
# share one identical retrieval core and a wipe-then-rebuild mid-experiment
# could make two cells run against silently non-identical corpora without
# either cell's run erroring out. Tests instead run against a wholly
# separate database, `financial_rag_test`, created once (`CREATE DATABASE
# financial_rag_test OWNER financial_rag;` inside the same container) — a
# different database on the same Postgres server is fully isolated
# (separate tables, separate storage) from `financial_rag`, not just a
# different table within it.
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
