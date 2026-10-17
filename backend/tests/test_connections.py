"""Shared connect-with-timeout helper: known issue 3, decision 16."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import psycopg
import pytest

from infra.connections import BoxUnavailable, connect_mongo, connect_postgres, connect_redis


def test_postgres_down_raises_box_unavailable_naming_postgres(monkeypatch):
    def _boom(*args, **kwargs):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", _boom)

    with pytest.raises(BoxUnavailable) as exc_info:
        connect_postgres("postgresql://nobody:nowhere@localhost:1/void", timeout=1)

    assert "Postgres" in str(exc_info.value)


def test_postgres_timeout_is_passed_to_the_driver(monkeypatch):
    seen = {}

    def _fake_connect(dsn, connect_timeout=None, **kwargs):
        seen["connect_timeout"] = connect_timeout
        return "a-connection"

    monkeypatch.setattr(psycopg, "connect", _fake_connect)

    result = connect_postgres("postgresql://x", timeout=7)

    assert result == "a-connection"
    assert seen["connect_timeout"] == 7


def test_postgres_up_returns_the_driver_connection(monkeypatch):
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: "a-connection")

    assert connect_postgres("postgresql://x") == "a-connection"


def test_mongo_without_the_package_installed_raises_box_unavailable_naming_mongodb(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymongo", None)  # simulate ImportError

    with pytest.raises(BoxUnavailable) as exc_info:
        connect_mongo("mongodb://localhost")

    assert "MongoDB" in str(exc_info.value)


def test_redis_without_the_package_installed_raises_box_unavailable_naming_redis(monkeypatch):
    monkeypatch.setitem(sys.modules, "redis", None)  # simulate ImportError

    with pytest.raises(BoxUnavailable) as exc_info:
        connect_redis("redis://localhost")

    assert "Redis" in str(exc_info.value)


def test_mongo_down_raises_box_unavailable_naming_mongodb(monkeypatch):
    fake_pymongo = ModuleType("pymongo")

    class _PyMongoError(Exception):
        pass

    fake_pymongo.errors = SimpleNamespace(PyMongoError=_PyMongoError)

    class _Admin:
        def command(self, *a, **k):
            raise _PyMongoError("no server")

    class _FakeClient:
        def __init__(self, *a, **k):
            self.admin = _Admin()

    fake_pymongo.MongoClient = _FakeClient
    monkeypatch.setitem(sys.modules, "pymongo", fake_pymongo)

    with pytest.raises(BoxUnavailable) as exc_info:
        connect_mongo("mongodb://localhost", timeout=1)

    assert "MongoDB" in str(exc_info.value)


def test_redis_down_raises_box_unavailable_naming_redis(monkeypatch):
    fake_redis = ModuleType("redis")

    class _RedisError(Exception):
        pass

    fake_redis.RedisError = _RedisError

    class _FakeClient:
        def ping(self):
            raise _RedisError("connection refused")

    fake_redis.from_url = lambda *a, **k: _FakeClient()
    monkeypatch.setitem(sys.modules, "redis", fake_redis)

    with pytest.raises(BoxUnavailable) as exc_info:
        connect_redis("redis://localhost", timeout=1)

    assert "Redis" in str(exc_info.value)


def test_postgres_timeout_is_a_true_wall_clock_cap_even_with_a_slow_driver(monkeypatch):
    """Regression: a hostname that resolves to two addresses (like
    "localhost" -> ::1 and 127.0.0.1) must not double the wait. Simulated
    here with a fake connect that ignores connect_timeout and just blocks."""
    import time

    def _slow_connect(dsn, connect_timeout=None, **kwargs):
        time.sleep(2)
        raise psycopg.OperationalError("simulating a slow, unreachable host")

    monkeypatch.setattr(psycopg, "connect", _slow_connect)

    start = time.monotonic()
    with pytest.raises(BoxUnavailable):
        connect_postgres("postgresql://x", timeout=0.3)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"took {elapsed:.2f}s, should have given up at ~0.3s"


def test_box_unavailable_message_is_plain_and_actionable():
    exc = BoxUnavailable("Postgres", "connection refused")

    message = str(exc)

    assert "Postgres" in message
    assert "Start it" in message
