"""Test/production database isolation (decided 2026-07-25). Must PASS.

`clean_postgres` (tests/conftest.py) truncates whatever database its DSN
points at. It pointed at the production `financial_rag` database until this
fix — pytest silently wiped a real 5-document index build twice while
building it. `conftest.py` already asserts this at import time (so an
accidental revert fails the whole suite immediately, loudly, at collection);
this test makes the same guarantee independently checkable and discoverable
without having to notice a mass collection failure to find out why.
"""
from __future__ import annotations

from conftest import PRODUCTION_POSTGRES_DB_NAME, POSTGRES_TEST_DSN, TEST_POSTGRES_DB_NAME


def test_test_db_name_differs_from_production_db_name():
    assert TEST_POSTGRES_DB_NAME != PRODUCTION_POSTGRES_DB_NAME


def test_test_dsn_does_not_reference_the_production_database():
    assert POSTGRES_TEST_DSN.rsplit("/", 1)[-1] != PRODUCTION_POSTGRES_DB_NAME


def test_test_dsn_targets_the_dedicated_test_database():
    assert POSTGRES_TEST_DSN.rsplit("/", 1)[-1] == TEST_POSTGRES_DB_NAME
