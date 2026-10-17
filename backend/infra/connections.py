"""One door in and one clear message out, for every box we talk to.

Every place that opens a database connection uses these functions instead
of calling the driver directly, so a stopped box fails fast with a plain
message instead of hanging the whole app.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import psycopg

DEFAULT_TIMEOUT_SECONDS = 10


class BoxUnavailable(RuntimeError):
    """A box (Postgres, MongoDB, Redis, ...) did not answer in time."""

    def __init__(self, box_name: str, detail: str):
        self.box_name = box_name
        super().__init__(
            f"{box_name} is not reachable right now. Start it and try again. ({detail})"
        )


def connect_postgres(dsn: str, timeout: int = DEFAULT_TIMEOUT_SECONDS, **kwargs) -> psycopg.Connection:
    """Connect with a hard wall-clock time limit; never let a stopped box hang the app.

    `connect_timeout` alone is not enough: a host like "localhost" can resolve
    to more than one address (IPv6 then IPv4), and psycopg/libpq applies that
    timeout to *each* address in turn, so the real wait can be a multiple of
    it. Running the connect in a worker and capping the wait ourselves makes
    the limit true regardless of how many addresses a name resolves to.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(psycopg.connect, dsn, connect_timeout=timeout, **kwargs)
    try:
        return future.result(timeout=timeout)
    except psycopg.OperationalError as exc:
        raise BoxUnavailable("Postgres", str(exc).strip()) from exc
    except FutureTimeoutError as exc:
        raise BoxUnavailable("Postgres", f"no reply within {timeout} seconds") from exc
    finally:
        # Don't wait for a still-running connect attempt (it may take up to
        # its own connect_timeout per address); let it finish on its own.
        pool.shutdown(wait=False)


def connect_mongo(uri: str, timeout: int = DEFAULT_TIMEOUT_SECONDS):
    """Placeholder until work package 2 adds the dictionary. Needs `pymongo`."""
    try:
        import pymongo
    except ImportError as exc:
        raise BoxUnavailable("MongoDB", "the pymongo package is not installed yet") from exc
    try:
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=timeout * 1000)
        client.admin.command("ping")
        return client
    except pymongo.errors.PyMongoError as exc:
        raise BoxUnavailable("MongoDB", str(exc).strip()) from exc


def connect_redis(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS):
    """Placeholder until work package 2 adds the dictionary cache. Needs `redis`."""
    try:
        import redis
    except ImportError as exc:
        raise BoxUnavailable("Redis", "the redis package is not installed yet") from exc
    try:
        client = redis.from_url(url, socket_connect_timeout=timeout)
        client.ping()
        return client
    except redis.RedisError as exc:
        raise BoxUnavailable("Redis", str(exc).strip()) from exc
