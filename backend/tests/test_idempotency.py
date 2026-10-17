"""infra/idempotency.py against the real test database. Step 0.4."""
from __future__ import annotations

import threading
import time

import psycopg
import pytest

import infra.idempotency as idempotency_module
from infra.idempotency import TicketConflict, TicketStillRunning, run_once


@pytest.fixture
def conn(clean_postgres):
    connection = psycopg.connect(clean_postgres, autocommit=True)
    connection.execute("DROP TABLE IF EXISTS idempotency_keys")
    yield connection
    connection.execute("DROP TABLE IF EXISTS idempotency_keys")
    connection.close()


@pytest.fixture
def second_conn(clean_postgres):
    connection = psycopg.connect(clean_postgres, autocommit=True)
    yield connection
    connection.close()


def test_a_fresh_ticket_runs_the_work_once(conn):
    calls = []
    outcome = run_once(conn, "ticket-1", {"file": "a.pdf"}, work=lambda: calls.append(1) or "done")

    assert outcome.result == "done"
    assert outcome.was_new_work is True
    assert calls == [1]


def test_same_ticket_same_content_runs_the_work_only_once(conn):
    calls = []
    work = lambda: calls.append(1) or "the-answer"

    first = run_once(conn, "ticket-1", {"q": "revenue?"}, work=work)
    second = run_once(conn, "ticket-1", {"q": "revenue?"}, work=work)

    assert first.result == "the-answer"
    assert second.result == "the-answer"
    assert second.was_new_work is False
    assert calls == [1]  # only ran once


def test_same_ticket_different_content_is_refused(conn):
    run_once(conn, "ticket-1", {"q": "revenue?"}, work=lambda: "answer-1")

    with pytest.raises(TicketConflict):
        run_once(conn, "ticket-1", {"q": "a completely different question"}, work=lambda: "answer-2")


def test_a_ticket_older_than_the_ttl_is_treated_as_new(conn):
    run_once(conn, "ticket-1", {"q": "revenue?"}, work=lambda: "old-answer")
    conn.execute("UPDATE idempotency_keys SET created_at = now() - interval '25 hours' WHERE ticket = 'ticket-1'")

    outcome = run_once(conn, "ticket-1", {"q": "revenue?"}, work=lambda: "new-answer", ttl_hours=24)

    assert outcome.result == "new-answer"
    assert outcome.was_new_work is True


def test_a_ticket_still_within_the_ttl_is_not_treated_as_new(conn):
    run_once(conn, "ticket-1", {"q": "revenue?"}, work=lambda: "first-answer")
    conn.execute("UPDATE idempotency_keys SET created_at = now() - interval '1 hour' WHERE ticket = 'ticket-1'")

    outcome = run_once(conn, "ticket-1", {"q": "revenue?"}, work=lambda: "second-answer", ttl_hours=24)

    assert outcome.result == "first-answer"
    assert outcome.was_new_work is False


def test_a_crashed_job_can_be_retried_with_the_same_ticket(conn, monkeypatch):
    """Job killed half way: it never reached 'done', so the row is stuck at
    'running'. Once it is stale, a retry with the same ticket must be able
    to claim it again rather than wait forever."""
    monkeypatch.setattr(idempotency_module, "_STALE_RUNNING_SECONDS", 0)
    with pytest.raises(RuntimeError):
        run_once(conn, "ticket-1", {"file": "a.pdf"}, work=lambda: (_ for _ in ()).throw(RuntimeError("power cut")))
    # simulate: crash happened AFTER the row was inserted but BEFORE our own
    # cleanup ran (the real crash case) -- put a stale "running" row back by hand
    conn.execute(
        "INSERT INTO idempotency_keys (ticket, content_hash, status, created_at) "
        "VALUES ('ticket-1', 'deadbeef', 'running', now() - interval '10 minutes')"
    )

    outcome = run_once(conn, "ticket-1", {"file": "a.pdf"}, work=lambda: "recovered")

    assert outcome.result == "recovered"
    assert outcome.was_new_work is True


def test_a_failed_attempt_does_not_block_a_clean_retry(conn):
    """The ordinary failure path: work() raises, the row is cleaned up
    immediately (not left stale), so a retry with the same ticket runs
    fresh right away."""
    with pytest.raises(RuntimeError):
        run_once(conn, "ticket-1", {"file": "a.pdf"}, work=lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    outcome = run_once(conn, "ticket-1", {"file": "a.pdf"}, work=lambda: "second-try-succeeds")

    assert outcome.result == "second-try-succeeds"
    assert outcome.was_new_work is True


def test_double_click_only_one_upload_runs(conn, second_conn):
    """Two callers use the same ticket at almost the same time (a double
    click). Only one should do the work; the other must get its result,
    not run a second time."""
    calls = []
    started = threading.Event()
    release = threading.Event()

    def slow_work():
        calls.append(1)
        started.set()
        release.wait(timeout=5)
        return "uploaded-once"

    results = {}

    def first_caller():
        results["first"] = run_once(conn, "ticket-1", {"file": "a.pdf"}, work=slow_work)

    thread = threading.Thread(target=first_caller)
    thread.start()
    started.wait(timeout=5)

    def second_caller():
        release.set()  # let the first one finish while the second is waiting/polling
        results["second"] = run_once(second_conn, "ticket-1", {"file": "a.pdf"}, work=lambda: calls.append(2) or "should-not-run")

    second_caller()
    thread.join(timeout=5)

    assert calls == [1]  # the second caller's work function never ran
    assert results["first"].result == "uploaded-once"
    assert results["second"].result == "uploaded-once"
    assert results["second"].was_new_work is False
