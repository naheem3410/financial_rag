"""infra/locks.py against the real test database. Step 0.4."""
from __future__ import annotations

import psycopg
import pytest

from infra.locks import LockBusy, acquire_lock, held_lock, release_lock


@pytest.fixture(autouse=True)
def _clean(clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    conn.execute("DROP TABLE IF EXISTS shared_locks")
    conn.close()
    yield


@pytest.fixture
def conn(clean_postgres):
    connection = psycopg.connect(clean_postgres, autocommit=True)
    yield connection
    connection.close()


def test_a_free_lock_can_be_taken(conn):
    acquire_lock(conn, "index:3M", holder="job-1", ttl_seconds=60)

    row = conn.execute("SELECT holder FROM shared_locks WHERE name = 'index:3M'").fetchone()
    assert row[0] == "job-1"


def test_two_writers_at_once_only_one_gets_it(conn):
    acquire_lock(conn, "index:3M", holder="job-1", ttl_seconds=60)

    with pytest.raises(LockBusy):
        acquire_lock(conn, "index:3M", holder="job-2", ttl_seconds=60)


def test_a_lock_frees_itself_once_its_time_runs_out(conn):
    acquire_lock(conn, "index:3M", holder="job-1", ttl_seconds=0)  # already expired

    acquire_lock(conn, "index:3M", holder="job-2", ttl_seconds=60)  # steals it, does not raise

    row = conn.execute("SELECT holder FROM shared_locks WHERE name = 'index:3M'").fetchone()
    assert row[0] == "job-2"


def test_releasing_lets_another_writer_in(conn):
    acquire_lock(conn, "index:3M", holder="job-1", ttl_seconds=60)
    release_lock(conn, "index:3M", holder="job-1")

    acquire_lock(conn, "index:3M", holder="job-2", ttl_seconds=60)  # does not raise

    row = conn.execute("SELECT holder FROM shared_locks WHERE name = 'index:3M'").fetchone()
    assert row[0] == "job-2"


def test_a_holder_cannot_release_a_lock_it_no_longer_owns(conn):
    """job-1's lock already expired and was stolen by job-2; job-1's late
    release must not remove job-2's active lock."""
    acquire_lock(conn, "index:3M", holder="job-1", ttl_seconds=0)
    acquire_lock(conn, "index:3M", holder="job-2", ttl_seconds=60)

    release_lock(conn, "index:3M", holder="job-1")

    row = conn.execute("SELECT holder FROM shared_locks WHERE name = 'index:3M'").fetchone()
    assert row[0] == "job-2"


def test_held_lock_context_manager_releases_on_success(conn):
    with held_lock(conn, "index:3M", holder="job-1", ttl_seconds=60):
        pass

    row = conn.execute("SELECT holder FROM shared_locks WHERE name = 'index:3M'").fetchone()
    assert row is None


def test_held_lock_context_manager_releases_even_on_error(conn):
    with pytest.raises(ValueError):
        with held_lock(conn, "index:3M", holder="job-1", ttl_seconds=60):
            raise ValueError("boom")

    row = conn.execute("SELECT holder FROM shared_locks WHERE name = 'index:3M'").fetchone()
    assert row is None


def test_different_names_do_not_block_each_other(conn):
    acquire_lock(conn, "index:3M", holder="job-1", ttl_seconds=60)

    acquire_lock(conn, "index:adobe", holder="job-2", ttl_seconds=60)  # does not raise
