"""Idempotency tickets for uploads and questions: send the same ticket twice
and the work runs once, the second call just gets the first call's result.
Send the same ticket with different content and it is refused, since that
usually means a mistake, not a genuine retry.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

DEFAULT_TTL_HOURS = 24
_STALE_RUNNING_SECONDS = 300  # a "running" row older than this is treated as an abandoned/crashed attempt

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    ticket TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class TicketConflict(RuntimeError):
    """Same ticket, different content -- refused rather than silently reused."""


class TicketStillRunning(RuntimeError):
    """Another caller is doing the work right now; try again shortly."""


@dataclass
class TicketOutcome:
    result: Any
    was_new_work: bool  # False means this call got someone else's cached result


def _hash_content(content: Any) -> str:
    canonical = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_idempotency_table(conn: psycopg.Connection) -> None:
    conn.execute(_SCHEMA)


def run_once(
    conn: psycopg.Connection,
    ticket: str,
    content: Any,
    work: Callable[[], Any],
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> TicketOutcome:
    """Runs `work()` at most once per ticket. Safe against a double click:
    the second caller waits briefly for the first to finish, then gets its
    result, instead of doing the work again."""
    ensure_idempotency_table(conn)
    content_hash = _hash_content(content)

    claimed = conn.execute(
        """
        INSERT INTO idempotency_keys (ticket, content_hash, status)
        VALUES (%(ticket)s, %(hash)s, 'running')
        ON CONFLICT (ticket) DO UPDATE SET content_hash = EXCLUDED.content_hash, status = 'running', created_at = now()
        WHERE idempotency_keys.created_at < now() - %(ttl)s * interval '1 hour'
           OR (idempotency_keys.status = 'running' AND idempotency_keys.created_at < now() - %(stale)s * interval '1 second')
        RETURNING content_hash
        """,
        {"ticket": ticket, "hash": content_hash, "ttl": ttl_hours, "stale": _STALE_RUNNING_SECONDS},
    ).fetchone()

    if claimed is not None:
        return _do_the_work(conn, ticket, content_hash, work)

    return _wait_for_existing(conn, ticket, content, content_hash, work, ttl_hours)


def _do_the_work(conn: psycopg.Connection, ticket: str, content_hash: str, work: Callable[[], Any]) -> TicketOutcome:
    try:
        result = work()
    except Exception:
        # Leave nothing "running" behind, so a retry with the same ticket can claim it again.
        conn.execute("DELETE FROM idempotency_keys WHERE ticket = %s AND content_hash = %s", (ticket, content_hash))
        raise
    conn.execute(
        "UPDATE idempotency_keys SET status = 'done', result = %s WHERE ticket = %s",
        (Jsonb(result), ticket),
    )
    return TicketOutcome(result=result, was_new_work=True)


def _wait_for_existing(
    conn: psycopg.Connection,
    ticket: str,
    content: Any,
    content_hash: str,
    work: Callable[[], Any],
    ttl_hours: int,
    max_wait_seconds: float = 5.0,
) -> TicketOutcome:
    deadline = time.monotonic() + max_wait_seconds
    while True:
        row = conn.execute(
            "SELECT content_hash, status, result FROM idempotency_keys WHERE ticket = %s", (ticket,)
        ).fetchone()
        if row is None:
            # Cleaned up between our failed claim and this read (rare) -- start over as if fresh.
            return run_once(conn, ticket, content, work, ttl_hours)
        existing_hash, status, result = row
        if existing_hash != content_hash:
            raise TicketConflict(f'ticket "{ticket}" was already used for different content')
        if status == "done":
            return TicketOutcome(result=result, was_new_work=False)
        if time.monotonic() >= deadline:
            raise TicketStillRunning(f'ticket "{ticket}" is still being worked on; try again shortly')
        time.sleep(0.1)
