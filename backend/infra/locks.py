"""One writer at a time for shared files (uploads, index rebuilds), backed by
a Postgres table so a lock survives even across separate connections/services.

A lock has a time limit (TTL). If the holder dies without releasing it, the
lock frees itself once the TTL passes -- nobody has to clean it up by hand.
"""
from __future__ import annotations

from contextlib import contextmanager

import psycopg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_locks (
    name TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
)
"""


class LockBusy(RuntimeError):
    """Someone else holds this lock right now."""


def ensure_lock_table(conn: psycopg.Connection) -> None:
    conn.execute(_SCHEMA)


def acquire_lock(conn: psycopg.Connection, name: str, holder: str, ttl_seconds: int) -> None:
    """Take the lock, or steal it if the previous holder's time ran out.
    Raises LockBusy if someone else genuinely still holds it."""
    ensure_lock_table(conn)
    row = conn.execute(
        """
        INSERT INTO shared_locks (name, holder, expires_at)
        VALUES (%(name)s, %(holder)s, now() + %(ttl)s * interval '1 second')
        ON CONFLICT (name) DO UPDATE SET holder = EXCLUDED.holder, expires_at = EXCLUDED.expires_at
        WHERE shared_locks.expires_at < now()
        RETURNING holder
        """,
        {"name": name, "holder": holder, "ttl": ttl_seconds},
    ).fetchone()
    if row is None:
        raise LockBusy(f'"{name}" is being worked on by someone else right now; try again shortly')


def release_lock(conn: psycopg.Connection, name: str, holder: str) -> None:
    """Only releases a lock this holder still owns -- a lock already stolen
    by someone else (because we took too long) is left alone."""
    conn.execute("DELETE FROM shared_locks WHERE name = %s AND holder = %s", (name, holder))


@contextmanager
def held_lock(conn: psycopg.Connection, name: str, holder: str, ttl_seconds: int):
    """`with held_lock(conn, "index:3M", "job-42", ttl_seconds=300): ...`"""
    acquire_lock(conn, name, holder, ttl_seconds)
    try:
        yield
    finally:
        release_lock(conn, name, holder)
