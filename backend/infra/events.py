"""One shape for events, and a swappable way to send and hear them.

An event says "this happened" -- for example `document.uploaded`. Whoever
listens for it does its own job when it hears one, so a person never waits
for the work itself, only for "got it". Today all of this runs inside one
process (`LocalEventBus`). Later it can be RabbitMQ, AWS SQS or AWS Lambda
instead -- same shape, same rules, picked by one setting (`get_event_bus`).
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import psycopg
from psycopg.types.json import Jsonb

DEFAULT_MAX_ATTEMPTS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_events (
    seq BIGSERIAL PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    job_id TEXT NOT NULL,
    step TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    time TIMESTAMPTZ NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_bus_events_type_seq ON bus_events (type, seq);

CREATE TABLE IF NOT EXISTS bus_cursors (
    listener_name TEXT PRIMARY KEY,
    last_seq BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bus_failures (
    event_id TEXT NOT NULL,
    listener_name TEXT NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    parked BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (event_id, listener_name)
);
"""


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    job_id: str
    time: str
    step: str = ""
    message: str = ""
    data: dict = field(default_factory=dict)


def new_event(type: str, job_id: str, step: str = "", message: str = "", data: Optional[dict] = None) -> Event:
    return Event(
        id=str(uuid.uuid4()), type=type, job_id=job_id, step=step, message=message,
        time=datetime.now(timezone.utc).isoformat(), data=data or {},
    )


class UnknownEventBus(ValueError):
    """A settings value named a bus type this app does not know."""


class EventBus(ABC):
    """A listener is a plain `Callable[[Event], None]` -- it must not import
    anything from this module, so it can run unchanged in a worker or a
    Lambda later."""

    @abstractmethod
    def publish(self, event: Event) -> None: ...

    @abstractmethod
    def subscribe(self, event_type: str, listener_name: str, handler: Callable) -> None: ...

    @abstractmethod
    def dispatch_pending(self, max_events: int = 100) -> int:
        """Delivers due events to every subscribed handler. Returns how many
        handler calls succeeded. Safe to call repeatedly (a worker loop)."""

    @abstractmethod
    def failed_events(self) -> list[tuple[str, str, str]]:
        """(event_id, listener_name, last_error) for everything parked."""

    @abstractmethod
    def replay(self, event_id: str, listener_name: str) -> None: ...

    @abstractmethod
    def events_since(self, job_id: str, after_seq: int, limit: int = 100) -> list[tuple[int, Event]]:
        """For a poller (like an SSE endpoint) rather than a registered
        listener: everything for this job after the given sequence number."""


class _PostgresBackedEventBus(EventBus):
    """Shared engine behind every bus name today. A real RabbitMQ/SQS/Lambda
    client will replace this per bus type without changing this class's
    public shape or any listener's code."""

    def __init__(self, conn: psycopg.Connection, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        self._conn = conn
        self._max_attempts = max_attempts
        self._subscriptions: dict[str, list[tuple[str, Callable]]] = {}
        conn.execute(_SCHEMA)

    def publish(self, event: Event) -> None:
        self._conn.execute(
            """
            INSERT INTO bus_events (id, type, job_id, step, message, time, data)
            VALUES (%(id)s, %(type)s, %(job_id)s, %(step)s, %(message)s, %(time)s, %(data)s)
            ON CONFLICT (id) DO NOTHING
            """,
            {
                "id": event.id, "type": event.type, "job_id": event.job_id, "step": event.step,
                "message": event.message, "time": event.time, "data": Jsonb(event.data),
            },
        )

    def subscribe(self, event_type: str, listener_name: str, handler: Callable) -> None:
        self._subscriptions.setdefault(event_type, []).append((listener_name, handler))

    def dispatch_pending(self, max_events: int = 100) -> int:
        delivered = 0
        for event_type, subs in self._subscriptions.items():
            for listener_name, handler in subs:
                delivered += self._dispatch_to_one_listener(event_type, listener_name, handler, max_events)
        return delivered

    def _dispatch_to_one_listener(self, event_type: str, listener_name: str, handler: Callable, max_events: int) -> int:
        delivered = 0
        cursor = self._cursor(listener_name)
        rows = self._conn.execute(
            "SELECT seq, id, type, job_id, step, message, time, data FROM bus_events "
            "WHERE type = %s AND seq > %s ORDER BY seq LIMIT %s",
            (event_type, cursor, max_events),
        ).fetchall()
        for seq, event_id, type_, job_id, step, message, time_, data in rows:
            if self._is_parked(event_id, listener_name):
                self._advance_cursor(listener_name, seq)
                continue
            event = Event(id=event_id, type=type_, job_id=job_id, step=step, message=message, time=str(time_), data=data)
            try:
                handler(event)
            except Exception as exc:
                attempts = self._record_failure(event_id, listener_name, str(exc))
                if attempts >= self._max_attempts:
                    self._park(event_id, listener_name)
                    self._advance_cursor(listener_name, seq)  # don't block everything behind it forever
                break  # stop this listener here so events stay in order; retry from the same one next time
            else:
                self._clear_failure(event_id, listener_name)
                self._advance_cursor(listener_name, seq)
                delivered += 1
        return delivered

    def failed_events(self) -> list[tuple[str, str, str]]:
        rows = self._conn.execute(
            "SELECT event_id, listener_name, last_error FROM bus_failures WHERE parked = TRUE"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def replay(self, event_id: str, listener_name: str) -> None:
        row = self._conn.execute(
            "SELECT type, job_id, step, message, time, data FROM bus_events WHERE id = %s", (event_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such event: {event_id}")
        type_, job_id, step, message, time_, data = row
        handler = self._find_handler(type_, listener_name)
        event = Event(id=event_id, type=type_, job_id=job_id, step=step, message=message, time=str(time_), data=data)
        handler(event)  # let it raise if it fails again -- caller decides what "replay failed" means
        self._clear_failure(event_id, listener_name)
        self._conn.execute(
            "UPDATE bus_failures SET parked = FALSE WHERE event_id = %s AND listener_name = %s",
            (event_id, listener_name),
        )

    def events_since(self, job_id: str, after_seq: int, limit: int = 100) -> list[tuple[int, Event]]:
        rows = self._conn.execute(
            "SELECT seq, id, type, job_id, step, message, time, data FROM bus_events "
            "WHERE job_id = %s AND seq > %s ORDER BY seq LIMIT %s",
            (job_id, after_seq, limit),
        ).fetchall()
        return [
            (seq, Event(id=eid, type=type_, job_id=jid, step=step, message=message, time=str(time_), data=data))
            for seq, eid, type_, jid, step, message, time_, data in rows
        ]

    def _find_handler(self, event_type: str, listener_name: str) -> Callable:
        for name, handler in self._subscriptions.get(event_type, []):
            if name == listener_name:
                return handler
        raise KeyError(f"no listener named {listener_name!r} for event type {event_type!r}")

    def _cursor(self, listener_name: str) -> int:
        row = self._conn.execute(
            "INSERT INTO bus_cursors (listener_name, last_seq) VALUES (%s, 0) "
            "ON CONFLICT (listener_name) DO UPDATE SET listener_name = EXCLUDED.listener_name "
            "RETURNING last_seq",
            (listener_name,),
        ).fetchone()
        return row[0]

    def _advance_cursor(self, listener_name: str, seq: int) -> None:
        self._conn.execute(
            "UPDATE bus_cursors SET last_seq = %s WHERE listener_name = %s", (seq, listener_name)
        )

    def _record_failure(self, event_id: str, listener_name: str, error: str) -> int:
        row = self._conn.execute(
            """
            INSERT INTO bus_failures (event_id, listener_name, attempts, last_error)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (event_id, listener_name) DO UPDATE SET
                attempts = bus_failures.attempts + 1, last_error = EXCLUDED.last_error
            RETURNING attempts
            """,
            (event_id, listener_name, error),
        ).fetchone()
        return row[0]

    def _clear_failure(self, event_id: str, listener_name: str) -> None:
        self._conn.execute(
            "DELETE FROM bus_failures WHERE event_id = %s AND listener_name = %s AND parked = FALSE",
            (event_id, listener_name),
        )

    def _park(self, event_id: str, listener_name: str) -> None:
        self._conn.execute(
            "UPDATE bus_failures SET parked = TRUE WHERE event_id = %s AND listener_name = %s",
            (event_id, listener_name),
        )

    def _is_parked(self, event_id: str, listener_name: str) -> bool:
        row = self._conn.execute(
            "SELECT parked FROM bus_failures WHERE event_id = %s AND listener_name = %s",
            (event_id, listener_name),
        ).fetchone()
        return bool(row and row[0])


class LocalEventBus(_PostgresBackedEventBus):
    """The in-website helper: no extra software, first choice for now."""


class RabbitMQEventBus(_PostgresBackedEventBus):
    """Placeholder: same behaviour as Local until a real `pika` connection is
    wired in. Exists so `get_event_bus("rabbitmq")` is a real, testable
    choice today, not just a name."""


class SqsEventBus(_PostgresBackedEventBus):
    """Placeholder: same behaviour as Local until a real `boto3` SQS client
    is wired in."""


class LambdaEventBus(_PostgresBackedEventBus):
    """Placeholder: same behaviour as Local until events are actually handed
    to AWS Lambda invocations."""


_BUS_TYPES = {
    "local": LocalEventBus,
    "rabbitmq": RabbitMQEventBus,
    "sqs": SqsEventBus,
    "lambda": LambdaEventBus,
}


def get_event_bus(name: str, conn: psycopg.Connection, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> EventBus:
    """The one place a setting turns into a bus. Unknown name -> a clear
    error naming the choices, not a silent fallback."""
    try:
        bus_class = _BUS_TYPES[name]
    except KeyError:
        raise UnknownEventBus(f'unknown event bus "{name}"; choose one of {sorted(_BUS_TYPES)}') from None
    return bus_class(conn, max_attempts=max_attempts)
