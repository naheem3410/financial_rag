"""DB isolation guard. clean_postgres used to point at production and wiped
a real index build twice; this makes that guarantee independently checkable."""
from __future__ import annotations

from conftest import PRODUCTION_POSTGRES_DB_NAME, POSTGRES_TEST_DSN, TEST_POSTGRES_DB_NAME


def test_test_db_name_differs_from_production_db_name():
    assert TEST_POSTGRES_DB_NAME != PRODUCTION_POSTGRES_DB_NAME


def test_test_dsn_does_not_reference_the_production_database():
    assert POSTGRES_TEST_DSN.rsplit("/", 1)[-1] != PRODUCTION_POSTGRES_DB_NAME


def test_test_dsn_targets_the_dedicated_test_database():
    assert POSTGRES_TEST_DSN.rsplit("/", 1)[-1] == TEST_POSTGRES_DB_NAME
