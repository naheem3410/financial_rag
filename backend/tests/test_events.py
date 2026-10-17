"""infra/events.py against the real test database. Step 0.5.

Parametrized over all four bus names so "swap Local for a fake RabbitMQ,
SQS or Lambda" is a real, run test, not just a claim.
"""
from __future__ import annotations

import ast
from pathlib import Path

import psycopg
import pytest

from infra.events import UnknownEventBus, get_event_bus, new_event

BUS_NAMES = ["local", "rabbitmq", "sqs", "lambda"]


@pytest.fixture(autouse=True)
def _clean(clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    for table in ("bus_events", "bus_cursors", "bus_failures"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.close()


@pytest.fixture(params=BUS_NAMES)
def bus(request, clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    yield get_event_bus(request.param, conn)
    conn.close()


def test_unknown_bus_name_raises_a_clear_error(clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    try:
        with pytest.raises(UnknownEventBus, match="unknown event bus"):
            get_event_bus("carrier-pigeon", conn)
    finally:
        conn.close()


def test_three_events_arrive_in_order(bus):
    received = []
    bus.subscribe("document.uploaded", "recorder", received.append)

    bus.publish(new_event("document.uploaded", job_id="job-1", step="1", message="first"))
    bus.publish(new_event("document.uploaded", job_id="job-1", step="2", message="second"))
    bus.publish(new_event("document.uploaded", job_id="job-1", step="3", message="third"))
    bus.dispatch_pending()

    assert [e.message for e in received] == ["first", "second", "third"]


def test_publishing_the_same_event_twice_is_handled_once(bus):
    received = []
    bus.subscribe("document.uploaded", "recorder", received.append)
    event = new_event("document.uploaded", job_id="job-1", message="only-once")

    bus.publish(event)
    bus.publish(event)  # same id, e.g. a retried publish call
    bus.dispatch_pending()

    assert len(received) == 1


def test_a_listener_that_fails_once_then_succeeds_is_retried_without_duplicating_earlier_work(bus):
    attempts = {"count": 0}

    def flaky(event):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("simulated crash")

    bus.subscribe("document.uploaded", "flaky-listener", flaky)
    bus.publish(new_event("document.uploaded", job_id="job-1", message="one"))

    bus.dispatch_pending()  # first attempt fails
    assert attempts["count"] == 1
    assert bus.failed_events() == []  # not parked yet, just one failed attempt

    bus.dispatch_pending()  # retried, succeeds this time
    assert attempts["count"] == 2

    bus.dispatch_pending()  # nothing left to redeliver
    assert attempts["count"] == 2  # not called a third time


def test_a_listener_that_keeps_failing_is_parked_after_max_attempts(bus):
    def always_fails(event):
        raise RuntimeError("still broken")

    bus.subscribe("document.uploaded", "broken-listener", always_fails)
    event = new_event("document.uploaded", job_id="job-1", message="doomed")
    bus.publish(event)

    for _ in range(5):
        bus.dispatch_pending()

    failed = bus.failed_events()
    assert len(failed) == 1
    assert failed[0][0] == event.id
    assert failed[0][1] == "broken-listener"
    assert "still broken" in failed[0][2]


def test_events_after_a_parked_one_still_get_delivered(bus):
    """A permanently broken listener for one event must not block every
    later event to it forever."""
    seen = []

    def sometimes_fails(event):
        if event.message == "doomed":
            raise RuntimeError("nope")
        seen.append(event.message)

    bus.subscribe("document.uploaded", "picky-listener", sometimes_fails)
    bus.publish(new_event("document.uploaded", job_id="job-1", message="doomed"))
    bus.publish(new_event("document.uploaded", job_id="job-1", message="fine"))

    for _ in range(5):
        bus.dispatch_pending()

    assert seen == ["fine"]
    assert len(bus.failed_events()) == 1


def test_replay_from_the_failed_pile_can_succeed(bus):
    should_fail = {"value": True}

    def sometimes(event):
        if should_fail["value"]:
            raise RuntimeError("not yet fixed")

    bus.subscribe("document.uploaded", "recovering-listener", sometimes)
    event = new_event("document.uploaded", job_id="job-1", message="will-be-fixed")
    bus.publish(event)
    for _ in range(5):
        bus.dispatch_pending()
    assert len(bus.failed_events()) == 1

    should_fail["value"] = False  # the underlying problem is fixed
    bus.replay(event.id, "recovering-listener")

    assert bus.failed_events() == []


def test_two_listeners_on_the_same_event_type_both_get_it(bus):
    seen_by_a, seen_by_b = [], []
    bus.subscribe("document.uploaded", "listener-a", seen_by_a.append)
    bus.subscribe("document.uploaded", "listener-b", seen_by_b.append)

    bus.publish(new_event("document.uploaded", job_id="job-1", message="hello"))
    bus.dispatch_pending()

    assert len(seen_by_a) == 1
    assert len(seen_by_b) == 1


def test_a_listener_only_hears_its_own_event_type(bus):
    uploaded_seen, indexed_seen = [], []
    bus.subscribe("document.uploaded", "uploaded-listener", uploaded_seen.append)
    bus.subscribe("document.indexed", "indexed-listener", indexed_seen.append)

    bus.publish(new_event("document.uploaded", job_id="job-1"))

    bus.dispatch_pending()

    assert len(uploaded_seen) == 1
    assert len(indexed_seen) == 0


def test_a_late_joining_browser_gets_everything_it_missed(bus):
    bus.publish(new_event("progress", job_id="job-1", message="step 1"))
    bus.publish(new_event("progress", job_id="job-1", message="step 2"))

    missed = bus.events_since("job-1", after_seq=0)

    assert [e.message for _, e in missed] == ["step 1", "step 2"]


def test_a_browser_that_drops_and_reconnects_continues_from_its_last_id(bus):
    bus.publish(new_event("progress", job_id="job-1", message="step 1"))
    bus.publish(new_event("progress", job_id="job-1", message="step 2"))
    first_batch = bus.events_since("job-1", after_seq=0)
    last_seq_seen = first_batch[-1][0]

    bus.publish(new_event("progress", job_id="job-1", message="step 3"))
    second_batch = bus.events_since("job-1", after_seq=last_seq_seen)

    assert [e.message for _, e in second_batch] == ["step 3"]


def test_events_since_only_returns_this_jobs_events(bus):
    bus.publish(new_event("progress", job_id="job-1", message="mine"))
    bus.publish(new_event("progress", job_id="job-2", message="not mine"))

    events = bus.events_since("job-1", after_seq=0)

    assert [e.message for _, e in events] == ["mine"]


def test_demo_listener_module_imports_no_bus_code():
    """A listener must be swappable into a worker or a Lambda unchanged, so
    it must not import anything from infra.events."""
    source_path = Path(__file__).resolve().parent.parent / "infra" / "events_demo_listener.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith("infra") for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("infra")


def test_demo_listener_works_as_a_real_handler(bus):
    from infra.events_demo_listener import handle_document_uploaded

    bus.subscribe("document.uploaded", "demo", lambda e: results.append(handle_document_uploaded(e)))
    results = []
    bus.publish(new_event("document.uploaded", job_id="job-42"))
    bus.dispatch_pending()

    assert results == ["would ingest job job-42"]
