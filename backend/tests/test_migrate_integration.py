"""db/migrate.py against the real project-scoped postgres:16.4 container,
using the dedicated test database (never production, see conftest.py)."""
from __future__ import annotations

import psycopg
import pytest

from db.migrate import MigrationError, run_migrations


def _write(dir_, name, sql):
    (dir_ / name).write_text(sql, encoding="utf-8")


def _reset_tracking_table(dsn: str) -> None:
    conn = psycopg.connect(dsn, autocommit=True)
    conn.execute("DROP TABLE IF EXISTS schema_migrations")
    conn.execute("DROP TABLE IF EXISTS migration_smoke_test")
    conn.close()


@pytest.fixture
def migrations_dir(tmp_path):
    d = tmp_path / "migrations"
    d.mkdir()
    return d


@pytest.fixture
def backup_dir(tmp_path):
    return tmp_path / "backups"


@pytest.fixture(autouse=True)
def _clean(clean_postgres):
    _reset_tracking_table(clean_postgres)
    yield
    _reset_tracking_table(clean_postgres)


def test_a_fresh_migration_runs_and_is_recorded(clean_postgres, migrations_dir, backup_dir):
    _write(migrations_dir, "0001_create_smoke_table.sql", "CREATE TABLE migration_smoke_test (id INT);")

    result = run_migrations(clean_postgres, migrations_dir, backup_dir)

    assert result.applied == ["0001"]
    assert result.backup_path is not None and result.backup_path.stat().st_size > 0
    conn = psycopg.connect(clean_postgres, autocommit=True)
    exists = conn.execute("SELECT to_regclass('migration_smoke_test')").fetchone()[0]
    assert exists == "migration_smoke_test"


def test_running_twice_changes_nothing_the_second_time(clean_postgres, migrations_dir, backup_dir):
    _write(migrations_dir, "0001_create_smoke_table.sql", "CREATE TABLE migration_smoke_test (id INT);")

    first = run_migrations(clean_postgres, migrations_dir, backup_dir)
    second = run_migrations(clean_postgres, migrations_dir, backup_dir)

    assert first.applied == ["0001"]
    assert second.applied == []
    assert second.skipped_already_applied == ["0001"]
    assert second.backup_path is None  # nothing pending, so no backup was taken


def test_a_failing_script_is_fully_rolled_back(clean_postgres, migrations_dir, backup_dir):
    _write(migrations_dir, "0001_broken.sql", "CREATE TABLE migration_smoke_test (id INT); THIS IS NOT SQL;")

    with pytest.raises(MigrationError):
        run_migrations(clean_postgres, migrations_dir, backup_dir)

    conn = psycopg.connect(clean_postgres, autocommit=True)
    exists = conn.execute("SELECT to_regclass('migration_smoke_test')").fetchone()[0]
    assert exists is None  # the CREATE TABLE before the bad statement was rolled back too
    tracked = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert tracked == []  # never recorded as applied


def test_propositions_count_unchanged_by_an_unrelated_migration(clean_postgres, migrations_dir, backup_dir):
    from contracts.models import Confidence, NumericRecord, Period, Proposition, PropositionMetadata
    from offline.indexing.metadata_store_postgres import PostgresMetadataStore

    store = PostgresMetadataStore(clean_postgres)
    store.upsert(Proposition(
        proposition_id="p_migrate_test", text="Acme net income was $1.", provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        numeric=NumericRecord(value=1.0, scale="unit", currency="USD", unit="absolute", period=Period(fiscal_year=2024)),
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="Acme", filing_type="10-Q", filing_date="2024-01-01", section="MD&A", page=1, source_uri="acme.pdf"),
    ))
    store.close()
    _write(migrations_dir, "0001_create_smoke_table.sql", "CREATE TABLE migration_smoke_test (id INT);")

    run_migrations(clean_postgres, migrations_dir, backup_dir)

    conn = psycopg.connect(clean_postgres, autocommit=True)
    count = conn.execute("SELECT COUNT(*) FROM propositions").fetchone()[0]
    assert count == 1


def test_a_script_that_shrinks_propositions_without_the_marker_is_refused(clean_postgres, migrations_dir, backup_dir):
    from contracts.models import Confidence, NumericRecord, Period, Proposition, PropositionMetadata
    from offline.indexing.metadata_store_postgres import PostgresMetadataStore

    store = PostgresMetadataStore(clean_postgres)
    store.upsert(Proposition(
        proposition_id="p_migrate_test", text="Acme net income was $1.", provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        numeric=NumericRecord(value=1.0, scale="unit", currency="USD", unit="absolute", period=Period(fiscal_year=2024)),
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="Acme", filing_type="10-Q", filing_date="2024-01-01", section="MD&A", page=1, source_uri="acme.pdf"),
    ))
    store.close()
    _write(migrations_dir, "0001_oops_deletes_facts.sql", "DELETE FROM propositions;")

    with pytest.raises(MigrationError, match="shrink propositions"):
        run_migrations(clean_postgres, migrations_dir, backup_dir)

    conn = psycopg.connect(clean_postgres, autocommit=True)
    count = conn.execute("SELECT COUNT(*) FROM propositions").fetchone()[0]
    assert count == 1  # rolled back, nothing lost


def test_a_script_with_the_marker_is_allowed_to_shrink_propositions(clean_postgres, migrations_dir, backup_dir):
    from contracts.models import Confidence, NumericRecord, Period, Proposition, PropositionMetadata
    from offline.indexing.metadata_store_postgres import PostgresMetadataStore

    store = PostgresMetadataStore(clean_postgres)
    store.upsert(Proposition(
        proposition_id="p_migrate_test", text="Acme net income was $1.", provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        numeric=NumericRecord(value=1.0, scale="unit", currency="USD", unit="absolute", period=Period(fiscal_year=2024)),
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="Acme", filing_type="10-Q", filing_date="2024-01-01", section="MD&A", page=1, source_uri="acme.pdf"),
    ))
    store.close()
    _write(migrations_dir, "0001_intentional_cleanup.sql", "-- allow-propositions-change\nDELETE FROM propositions;")

    result = run_migrations(clean_postgres, migrations_dir, backup_dir)

    assert result.applied == ["0001"]
    conn = psycopg.connect(clean_postgres, autocommit=True)
    count = conn.execute("SELECT COUNT(*) FROM propositions").fetchone()[0]
    assert count == 0


def test_backup_is_skipped_when_nothing_is_pending(clean_postgres, migrations_dir, backup_dir):
    result = run_migrations(clean_postgres, migrations_dir, backup_dir)

    assert result.applied == []
    assert result.backup_path is None


def test_two_migrations_run_in_order(clean_postgres, migrations_dir, backup_dir):
    _write(migrations_dir, "0001_create_smoke_table.sql", "CREATE TABLE migration_smoke_test (id INT);")
    _write(migrations_dir, "0002_add_a_column.sql", "ALTER TABLE migration_smoke_test ADD COLUMN name TEXT;")

    result = run_migrations(clean_postgres, migrations_dir, backup_dir)

    assert result.applied == ["0001", "0002"]


def test_backup_failure_refuses_to_run_any_migration(clean_postgres, migrations_dir, backup_dir):
    """A container name that does not exist simulates the backup tool being
    unreachable: nothing should run, not even the first script."""
    _write(migrations_dir, "0001_create_smoke_table.sql", "CREATE TABLE migration_smoke_test (id INT);")

    with pytest.raises(MigrationError, match="backup"):
        run_migrations(clean_postgres, migrations_dir, backup_dir, container="no_such_container")

    conn = psycopg.connect(clean_postgres, autocommit=True)
    exists = conn.execute("SELECT to_regclass('migration_smoke_test')").fetchone()[0]
    assert exists is None
    tracked = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert tracked == []


def test_a_second_runner_gives_up_cleanly_while_the_first_holds_the_lock(clean_postgres, migrations_dir, backup_dir, monkeypatch):
    """Simulates two uploads/scripts racing: hold the advisory lock from
    outside, then a real run must wait briefly and give up, not corrupt
    anything or apply the migration twice."""
    import db.migrate as migrate_module

    monkeypatch.setattr(migrate_module, "_LOCK_WAIT_SECONDS", 1)
    _write(migrations_dir, "0001_create_smoke_table.sql", "CREATE TABLE migration_smoke_test (id INT);")

    holder = psycopg.connect(clean_postgres, autocommit=True)
    holder.execute("SELECT pg_advisory_lock(%s)", (migrate_module._ADVISORY_LOCK_KEY,))
    try:
        with pytest.raises(MigrationError, match="already in progress"):
            run_migrations(clean_postgres, migrations_dir, backup_dir)
    finally:
        holder.execute("SELECT pg_advisory_unlock(%s)", (migrate_module._ADVISORY_LOCK_KEY,))
        holder.close()

    conn = psycopg.connect(clean_postgres, autocommit=True)
    exists = conn.execute("SELECT to_regclass('migration_smoke_test')").fetchone()[0]
    assert exists is None  # the waiting runner never got to apply it

    # once the lock is free, a normal run still works and applies it exactly once
    result = run_migrations(clean_postgres, migrations_dir, backup_dir)
    assert result.applied == ["0001"]


def test_production_database_name_is_never_used_in_this_test_file(clean_postgres):
    """Guard against accidentally pointing an integration test at the real DB."""
    assert "financial_rag_v2" not in clean_postgres
    assert clean_postgres.endswith("/financial_rag_test")
