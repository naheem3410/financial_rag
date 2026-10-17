"""Pure-function pieces of db/migrate.py -- no database needed. Step 0.3."""
from __future__ import annotations

from pathlib import Path

import pytest

from db.migrate import (
    MigrationError,
    allows_propositions_change,
    backup_command,
    discover_migrations,
    migration_version,
    pending_migrations,
)


def _touch(dir_: Path, name: str, content: str = "SELECT 1;") -> Path:
    path = dir_ / name
    path.write_text(content, encoding="utf-8")
    return path


def test_migration_version_reads_the_number_prefix():
    assert migration_version(Path("0007_add_metric_labels.sql")) == "0007"


def test_migration_version_rejects_a_badly_named_file():
    with pytest.raises(MigrationError):
        migration_version(Path("add_metric_labels.sql"))


def test_discover_migrations_sorts_by_number(tmp_path):
    _touch(tmp_path, "0002_second.sql")
    _touch(tmp_path, "0001_first.sql")

    files = discover_migrations(tmp_path)

    assert [f.name for f in files] == ["0001_first.sql", "0002_second.sql"]


def test_discover_migrations_ignores_non_sql_files(tmp_path):
    _touch(tmp_path, "0001_first.sql")
    (tmp_path / "README.md").write_text("notes", encoding="utf-8")

    files = discover_migrations(tmp_path)

    assert [f.name for f in files] == ["0001_first.sql"]


def test_discover_migrations_refuses_a_duplicate_number(tmp_path):
    _touch(tmp_path, "0001_first.sql")
    _touch(tmp_path, "0001_also_first.sql")

    with pytest.raises(MigrationError):
        discover_migrations(tmp_path)


def test_pending_migrations_skips_already_applied(tmp_path):
    first = _touch(tmp_path, "0001_first.sql")
    second = _touch(tmp_path, "0002_second.sql")

    pending = pending_migrations([first, second], applied_versions={"0001"})

    assert pending == [second]


def test_pending_migrations_empty_when_all_applied(tmp_path):
    first = _touch(tmp_path, "0001_first.sql")

    pending = pending_migrations([first], applied_versions={"0001"})

    assert pending == []


def test_allows_propositions_change_marker_on_first_line():
    sql = "-- allow-propositions-change\nDELETE FROM propositions WHERE company = 'test';"

    assert allows_propositions_change(sql) is True


def test_allows_propositions_change_false_without_the_marker():
    sql = "CREATE TABLE documents (id TEXT PRIMARY KEY);"

    assert allows_propositions_change(sql) is False


def test_allows_propositions_change_ignores_blank_lines_before_the_marker():
    sql = "\n\n-- allow-propositions-change\nDELETE FROM propositions;"

    assert allows_propositions_change(sql) is True


def test_allows_propositions_change_marker_must_be_the_first_real_line():
    sql = "CREATE TABLE t (id TEXT);\n-- allow-propositions-change\n"

    assert allows_propositions_change(sql) is False


def test_backup_command_runs_pg_dump_inside_the_named_container():
    command = backup_command("financial_rag_postgres", "financial_rag", "financial_rag_v2")

    assert command == ["docker", "exec", "financial_rag_postgres", "pg_dump", "-U", "financial_rag", "-d", "financial_rag_v2"]
