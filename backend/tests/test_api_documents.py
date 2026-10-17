"""api/server.py's upload endpoints (step 3.4), against a real test
database and a real event bus -- the fake pieces are only the parts of
Runtime the two endpoints under test never touch (the finance pipeline).
"""
from __future__ import annotations

import threading

import psycopg
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from api.server import Runtime, app
from infra.events import LocalEventBus
from offline.documents.file_store import InMemoryFileStore
from offline.documents.policy_fact_store import PolicyFactStore


@pytest.fixture(autouse=True)
def _clean(clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    for table in ("documents", "policy_facts", "bus_events", "bus_cursors", "bus_failures", "shared_locks", "idempotency_keys"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.close()


@pytest.fixture
def client(clean_postgres):
    documents_conn = psycopg.connect(clean_postgres, autocommit=True)
    rt = Runtime(
        config=None, embedder=None, reranker=None, bundle=None, registry=[], clients={}, models={},
        restore_names=lambda s: s, info={}, lock=threading.Lock(),
        postgres_dsn=clean_postgres,
        documents_conn=documents_conn, file_store=InMemoryFileStore(),
        policy_fact_store=PolicyFactStore(clean_postgres), documents_bus=LocalEventBus(documents_conn),
        antecedent_llm_client=None,
    )
    app.state.runtime = rt
    test_client = TestClient(app)
    yield test_client
    rt.policy_fact_store.close()
    documents_conn.close()


def _upload(client, ticket="t-1", title="Account Handbook", doc_type="policy", file_name="handbook.txt", file_bytes=b"4.1 Eligibility\nApplicants must be 18 or older.\n", **extra):
    data = {"ticket": ticket, "title": title, "doc_type": doc_type, **extra}
    return client.post("/api/documents", data=data, files={"file": (file_name, file_bytes, "text/plain")})


def _wait_for_done(client, doc_id, timeout=5.0):
    """Polls like the real upload screen would, but accumulates every event
    seen across polls (not just the last poll's batch) so a test can check
    the whole history, e.g. that "uploaded" arrived before "indexed"."""
    import time

    deadline = time.monotonic() + timeout
    after_seq = 0
    all_events: list[dict] = []
    while time.monotonic() < deadline:
        response = client.get(f"/api/documents/{doc_id}/events", params={"after_seq": after_seq})
        body = response.json()
        all_events.extend(body["events"])
        if body["events"]:
            after_seq = body["events"][-1]["seq"]
        if body["done"]:
            return {"events": all_events, "done": True, "failed": body["failed"]}
        time.sleep(0.02)
    raise AssertionError("upload did not finish in time")


def test_uploading_a_document_returns_accepted_at_once(client):
    response = _upload(client)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["doc_id"]


def test_the_upload_finishes_and_progress_can_be_watched_through_the_events_endpoint(client):
    response = _upload(client)
    doc_id = response.json()["doc_id"]

    final = _wait_for_done(client, doc_id)

    assert final["failed"] is False
    event_types = [e["type"] for e in final["events"]]
    assert "document.uploaded" in event_types
    assert "document.indexed" in event_types


def test_missing_title_field_entirely_is_refused_by_fastapi_itself(client):
    response = client.post(
        "/api/documents",
        data={"ticket": "t-1", "doc_type": "policy"},  # title left out entirely
        files={"file": ("h.txt", b"4.1 Eligibility\nMust be 18.\n", "text/plain")},
    )

    assert response.status_code == 422


def test_a_truly_empty_title_is_refused_by_fastapis_own_required_field_check(client):
    """An empty string form value is treated as "missing" by FastAPI's own
    Form(...) validation before our own code ever sees it -- a real
    framework quirk, confirmed here rather than assumed."""
    response = _upload(client, title="")

    assert response.status_code == 422
    assert any(err["loc"][-1] == "title" for err in response.json()["detail"])


def test_a_whitespace_only_title_is_refused_by_our_own_check_with_a_plain_message(client):
    """Non-empty (so it passes FastAPI's own required-field check), but
    blank once stripped -- this one genuinely reaches DocumentCardError."""
    response = _upload(client, title="   ")

    assert response.status_code == 422
    assert response.json()["detail"] == "a document needs a title"


def test_a_document_type_not_on_the_pick_list_is_refused(client):
    response = _upload(client, doc_type="not-a-real-type")

    assert response.status_code == 422


def test_double_click_same_ticket_returns_the_same_document(client):
    first = _upload(client, ticket="same-ticket")
    second = _upload(client, ticket="same-ticket")

    assert first.json()["doc_id"] == second.json()["doc_id"]


def test_events_endpoint_for_an_unknown_document_returns_an_empty_not_done_result(client):
    response = client.get("/api/documents/no-such-doc/events")

    assert response.status_code == 200
    body = response.json()
    assert body["events"] == []
    assert body["done"] is False


def test_a_failing_ingest_is_reported_as_a_failed_event_through_the_api(client, monkeypatch):
    import api.server as server_module

    def _always_fails(card, data):
        raise RuntimeError("simulated model failure")

    monkeypatch.setattr(server_module, "ingest_policy_document", lambda card, data, store, antecedent_llm_client=None: _always_fails(card, data))

    response = _upload(client)
    doc_id = response.json()["doc_id"]

    final = _wait_for_done(client, doc_id)

    assert final["failed"] is True
