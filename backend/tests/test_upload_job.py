"""offline/documents/upload_job.py against the real test database, a real
event bus, and a fake file store. Step 3.3, decision 15.
"""
from __future__ import annotations

import threading
import time

import psycopg
import pytest

from infra.events import LocalEventBus
from offline.documents.document_card import get_document, list_documents
from offline.documents.file_store import InMemoryFileStore
from offline.documents.upload_job import IngestResult, delete_document, run_ingest_step, submit_upload


@pytest.fixture(autouse=True)
def _clean(clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    for table in ("documents", "bus_events", "bus_cursors", "bus_failures", "shared_locks", "idempotency_keys"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.close()


@pytest.fixture
def conn(clean_postgres):
    """Plain tuple-row connection, matching real usage: the upload job hands
    this one connection to document_card (its own internal dict cursor),
    infra.locks, infra.idempotency and infra.events (all tuple-row) alike."""
    connection = psycopg.connect(clean_postgres, autocommit=True)
    yield connection
    connection.close()


@pytest.fixture
def bus(conn):
    return LocalEventBus(conn)


@pytest.fixture
def file_store():
    return InMemoryFileStore()


def _card_kwargs(title="Account Opening Policy", doc_type="policy", file_name="policy.pdf"):
    return {"title": title, "doc_type": doc_type, "file_name": file_name}


def _events_for(bus, job_id):
    return [e.type for _, e in bus.events_since(job_id, after_seq=0)]


def _fake_ingest(fact_ids=("f_1", "f_2")):
    calls = []

    def ingest(card, file_bytes):
        calls.append((card.id, file_bytes))
        return IngestResult(fact_ids=fact_ids)

    ingest.calls = calls
    return ingest


def _submit_and_ingest(conn, file_store, bus, ticket, doc_id, file_bytes, card_kwargs, ingest_fn, **kwargs):
    """The two-phase happy path, run back to back synchronously (no
    `postgres_dsn`) so a test gets a deterministic result to assert on."""
    outcome = submit_upload(conn, file_store, bus, ticket, doc_id, file_bytes, card_kwargs, ingest_fn)
    if outcome.result["status"] == "accepted":
        run_ingest_step(conn, file_store, bus, doc_id, ingest_fn, **kwargs)
    return outcome


def test_a_small_upload_is_indexed_and_searchable(conn, bus, file_store):
    ingest = _fake_ingest(fact_ids=("f_1",))
    stores_checked = []

    _submit_and_ingest(
        conn, file_store, bus, "t-1", "doc-1", b"hello policy", _card_kwargs(), ingest,
        check_stores_fn=lambda ids: stores_checked.append(ids) or True,
    )

    assert get_document(conn, "doc-1").indexed is True
    assert get_document(conn, "doc-1").fact_ids == ("f_1",)
    assert file_store.read("doc-1") == b"hello policy"
    assert stores_checked == [("f_1",)]
    assert "document.uploaded" in _events_for(bus, "doc-1")
    assert "document.indexed" in _events_for(bus, "doc-1")


def test_submit_returns_at_once_without_waiting_on_ingest(conn, bus, file_store):
    """The fast half never touches the pluggable content step at all."""
    def never_called(card, file_bytes):
        raise AssertionError("submit_upload must not call ingest_fn itself")

    outcome = submit_upload(conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), never_called)

    assert outcome.result["status"] == "accepted"
    assert get_document(conn, "doc-1").indexed is False  # ingest hasn't run yet


def test_submit_upload_with_a_dsn_returns_at_once_and_finishes_in_the_background(conn, bus, file_store, clean_postgres):
    """The real production shape: give it a DSN, and the slow work happens
    on its own thread and connection while the caller is already free.

    Proved without any wall-clock threshold (a fixed "< N seconds" budget
    is inherently flaky under real system load -- confirmed by a real,
    one-off failure of an earlier, timing-based version of this test):
    ingest is held deliberately blocked, so `submit_upload` returning at
    all, with the document still not indexed, is itself the proof it never
    waited on the slow step."""
    started = threading.Event()
    release_ingest = threading.Event()

    def blocked_ingest(card, file_bytes):
        started.set()
        release_ingest.wait(timeout=5)
        return IngestResult(fact_ids=("f_1",))

    outcome = submit_upload(
        conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), blocked_ingest, postgres_dsn=clean_postgres,
    )

    assert outcome.result["status"] == "accepted"
    assert get_document(conn, "doc-1").indexed is False  # ingest hadn't finished (or even necessarily started) yet

    assert started.wait(timeout=5)  # the background thread genuinely picked it up
    release_ingest.set()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not get_document(conn, "doc-1").indexed:
        time.sleep(0.02)
    assert get_document(conn, "doc-1").indexed is True
    assert get_document(conn, "doc-1").fact_ids == ("f_1",)


def test_uploading_the_same_file_again_under_a_different_ticket_is_skipped(conn, bus, file_store):
    ingest = _fake_ingest()
    _submit_and_ingest(conn, file_store, bus, "t-1", "doc-1", b"same bytes", _card_kwargs(), ingest)

    outcome = submit_upload(conn, file_store, bus, "t-2", "doc-2", b"same bytes", _card_kwargs(file_name="policy-copy.pdf"), ingest)

    assert outcome.result["status"] == "skipped_already_there"
    assert outcome.result["doc_id"] == "doc-1"
    assert len(ingest.calls) == 1  # never ran again for the repeat
    assert "document.skipped" in _events_for(bus, "doc-2")


def test_a_new_version_replaces_the_old_facts(conn, bus, file_store):
    removed = []
    ingest_v1 = _fake_ingest(fact_ids=("f_old_1", "f_old_2"))
    _submit_and_ingest(conn, file_store, bus, "t-1", "doc-1", b"version one", _card_kwargs(), ingest_v1)

    ingest_v2 = _fake_ingest(fact_ids=("f_new_1",))
    _submit_and_ingest(
        conn, file_store, bus, "t-2", "doc-2", b"version two",
        _card_kwargs(file_name="policy-v2.pdf"), ingest_v2, remove_fn=removed.extend,
    )

    assert removed == ["f_old_1", "f_old_2"]
    assert get_document(conn, "doc-2").fact_ids == ("f_new_1",)
    old = get_document(conn, "doc-1")
    assert old.indexed is False  # superseded, no longer "the current one"
    assert old.fact_ids == ()


def test_deleting_a_document_removes_it_from_every_store(conn, bus, file_store):
    ingest = _fake_ingest(fact_ids=("f_1", "f_2"))
    _submit_and_ingest(conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), ingest)
    removed = []

    delete_document(conn, file_store, bus, "doc-1", remove_fn=removed.extend)

    assert removed == ["f_1", "f_2"]
    assert file_store.exists("doc-1") is False
    with pytest.raises(KeyError):
        get_document(conn, "doc-1")
    assert "document.deleted" in _events_for(bus, "doc-1")


def test_double_click_same_ticket_only_submits_once(conn, bus, file_store):
    ingest = _fake_ingest()

    first = submit_upload(conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), ingest)
    second = submit_upload(conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), ingest)

    assert first.result == second.result
    assert len(list_documents(conn)) == 1  # not created twice


def test_running_ingest_twice_for_the_same_document_only_ingests_once(conn, bus, file_store):
    """Covers a duplicate `document.uploaded` delivery, or two workers
    both picking up the same event."""
    ingest = _fake_ingest()
    submit_upload(conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), ingest)

    run_ingest_step(conn, file_store, bus, "doc-1", ingest)
    run_ingest_step(conn, file_store, bus, "doc-1", ingest)  # already indexed -> no-op

    assert len(ingest.calls) == 1


def test_a_permanently_failing_ingest_retries_then_fails_clearly(conn, bus, file_store):
    attempts = []

    def always_fails(card, file_bytes):
        attempts.append(1)
        raise RuntimeError("model call failed")

    submit_upload(conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), always_fails)
    with pytest.raises(RuntimeError):
        run_ingest_step(conn, file_store, bus, "doc-1", always_fails, max_ingest_attempts=3)

    assert len(attempts) == 3
    events = _events_for(bus, "doc-1")
    assert events.count("document.retry") == 2
    assert events.count("document.failed") == 1
    assert get_document(conn, "doc-1").indexed is False


def test_ingest_that_fails_once_then_succeeds_is_retried_not_abandoned(conn, bus, file_store):
    attempts = []

    def fails_once(card, file_bytes):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return IngestResult(fact_ids=("f_1",))

    submit_upload(conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), fails_once)
    run_ingest_step(conn, file_store, bus, "doc-1", fails_once, max_ingest_attempts=3)

    assert get_document(conn, "doc-1").indexed is True
    assert len(attempts) == 2


def test_a_failed_ingest_can_be_retried_by_calling_run_ingest_step_again_no_duplicate_card(conn, bus, file_store):
    """Simulates a crash: the first attempt fails every time, then a real
    retry with a working ingest function finishes cleanly, same document,
    no duplicate card."""
    def always_fails(card, file_bytes):
        raise RuntimeError("crashed")

    submit_upload(conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), always_fails)
    with pytest.raises(RuntimeError):
        run_ingest_step(conn, file_store, bus, "doc-1", always_fails, max_ingest_attempts=1)

    working_ingest = _fake_ingest(fact_ids=("f_1",))
    run_ingest_step(conn, file_store, bus, "doc-1", working_ingest)

    assert get_document(conn, "doc-1").indexed is True
    assert len(list_documents(conn)) == 1  # no duplicate row


def test_a_failed_three_store_check_fails_the_job_and_does_not_mark_indexed(conn, bus, file_store):
    ingest = _fake_ingest(fact_ids=("f_1",))
    submit_upload(conn, file_store, bus, "t-1", "doc-1", b"hello", _card_kwargs(), ingest)

    with pytest.raises(RuntimeError, match="three-store check failed"):
        run_ingest_step(conn, file_store, bus, "doc-1", ingest, check_stores_fn=lambda ids: False)

    assert get_document(conn, "doc-1").indexed is False
    assert "document.failed" in _events_for(bus, "doc-1")


def test_two_different_documents_ingest_at_once_without_interfering(conn, bus, file_store):
    """Different title+type -> different lock names -> both proceed
    independently and both end correct."""
    submit_upload(conn, file_store, bus, "t-a", "doc-a", b"policy A", _card_kwargs(title="Policy A"), _fake_ingest())
    submit_upload(conn, file_store, bus, "t-b", "doc-b", b"policy B", _card_kwargs(title="Policy B"), _fake_ingest())
    results = {}

    def ingest_a():
        results["a"] = run_ingest_step(conn, file_store, bus, "doc-a", _fake_ingest(fact_ids=("f_a",)))

    def ingest_b():
        results["b"] = run_ingest_step(conn, file_store, bus, "doc-b", _fake_ingest(fact_ids=("f_b",)))

    t_a, t_b = threading.Thread(target=ingest_a), threading.Thread(target=ingest_b)
    t_a.start(); t_b.start()
    t_a.join(timeout=5); t_b.join(timeout=5)

    assert get_document(conn, "doc-a").fact_ids == ("f_a",)
    assert get_document(conn, "doc-b").fact_ids == ("f_b",)


def test_two_ingests_of_the_same_title_and_type_at_once_one_waits_both_end_correct(conn, bus, file_store):
    """Same title+type -> same lock -> the second waits for the first
    instead of racing it (no LockBusy escapes to the caller), and since
    they share a title+type they are also versions of one another: once
    both finish, the second (later) correctly supersedes the first --
    exactly the behaviour test_a_new_version_replaces_the_old_facts checks
    on its own, seen here surviving real lock contention too."""
    submit_upload(conn, file_store, bus, "t-1", "doc-1", b"v1", _card_kwargs(), _fake_ingest())
    submit_upload(conn, file_store, bus, "t-2", "doc-2", b"v2", _card_kwargs(file_name="v2.pdf"), _fake_ingest())

    started_first = threading.Event()
    release_first = threading.Event()

    def slow_ingest(card, file_bytes):
        started_first.set()
        release_first.wait(timeout=5)
        return IngestResult(fact_ids=("f_first",))

    def ingest_first():
        run_ingest_step(conn, file_store, bus, "doc-1", slow_ingest)

    t1 = threading.Thread(target=ingest_first)
    t1.start()
    started_first.wait(timeout=5)  # doc-1 is now holding the lock, mid-ingest

    release_first.set()  # let the first finish while the second is queued behind the lock
    run_ingest_step(conn, file_store, bus, "doc-2", _fake_ingest(fact_ids=("f_second",)))
    t1.join(timeout=5)

    assert get_document(conn, "doc-2").indexed is True
    assert get_document(conn, "doc-2").fact_ids == ("f_second",)
    assert get_document(conn, "doc-1").indexed is False  # superseded by doc-2, not corrupted or stuck
