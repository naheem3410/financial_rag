"""Runs database change scripts safely: backup first, one at a time, safe to
run twice, and never leaves the `propositions` table smaller by accident.

Step 0.3 of the fix plan. See db/migrations/README.md for how to add a script.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.conninfo import conninfo_to_dict

from infra.connections import connect_postgres

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent / "backups"
_ADVISORY_LOCK_KEY = 727272  # arbitrary, just needs to be the same everywhere
_LOCK_WAIT_SECONDS = 30
_VERSION_RE = re.compile(r"^(\d{4})_.+\.sql$")


class MigrationError(RuntimeError):
    """A migration script failed, or the runner refused to run one."""


@dataclass
class RunResult:
    applied: list[str] = field(default_factory=list)
    skipped_already_applied: list[str] = field(default_factory=list)
    backup_path: Optional[Path] = None


def migration_version(path: Path) -> str:
    """"0007_add_metric_labels.sql" -> "0007". Raises if the name is wrong."""
    match = _VERSION_RE.match(path.name)
    if not match:
        raise MigrationError(f"{path.name} does not match NNNN_description.sql")
    return match.group(1)


def discover_migrations(migrations_dir: Path) -> list[Path]:
    """All migration files, in run order. Refuses two files with the same number."""
    files = sorted(migrations_dir.glob("*.sql"))
    seen: dict[str, Path] = {}
    for path in files:
        version = migration_version(path)
        if version in seen:
            raise MigrationError(f"two migrations share number {version}: {seen[version].name} and {path.name}")
        seen[version] = path
    return files


def pending_migrations(all_migrations: list[Path], applied_versions: set[str]) -> list[Path]:
    return [path for path in all_migrations if migration_version(path) not in applied_versions]


def allows_propositions_change(sql_text: str) -> bool:
    """A script opts in to shrinking `propositions` with a marker on its first line."""
    for line in sql_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped == "-- allow-propositions-change"
    return False


def backup_command(container: str, db_user: str, db_name: str) -> list[str]:
    """The `docker exec ... pg_dump` argv. Runs inside the container so no
    local pg_dump install is needed on Windows."""
    return ["docker", "exec", container, "pg_dump", "-U", db_user, "-d", db_name]


def _take_backup(container: str, db_user: str, db_name: str, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = backup_dir / f"{db_name}_{stamp}.sql"
    try:
        with out_path.open("wb") as out_file:
            result = subprocess.run(
                backup_command(container, db_user, db_name),
                stdout=out_file, stderr=subprocess.PIPE, timeout=120,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationError(f"backup could not run ({exc}); refusing to touch the database") from exc
    if result.returncode != 0 or out_path.stat().st_size == 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise MigrationError(f"backup failed ({detail or 'empty output'}); refusing to touch the database")
    return out_path


def _ensure_tracking_table(conn: psycopg.Connection) -> None:
    """`conn` is autocommit, so this DDL takes effect immediately -- no
    explicit commit (psycopg3 refuses .commit() in autocommit mode)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _applied_versions(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def _acquire_lock(conn: psycopg.Connection) -> None:
    """One runner at a time. A second runner waits briefly, then gives up
    with a clear message rather than racing the first one."""
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        got_it = conn.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)).fetchone()[0]
        if got_it:
            return
        if time.monotonic() >= deadline:
            raise MigrationError("another migration run is already in progress; try again shortly")
        time.sleep(1)


def _propositions_count(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM propositions").fetchone()
    return row[0] if row else 0


def run_migrations(
    dsn: str,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    container: str = "financial_rag_postgres",
) -> RunResult:
    result = RunResult()
    all_migrations = discover_migrations(migrations_dir)

    conn = connect_postgres(dsn, autocommit=True)
    try:
        _ensure_tracking_table(conn)
        _acquire_lock(conn)
        try:
            applied = _applied_versions(conn)
            result.skipped_already_applied = [migration_version(p) for p in all_migrations if migration_version(p) in applied]
            pending = pending_migrations(all_migrations, applied)
            if not pending:
                return result

            info = conninfo_to_dict(dsn)
            db_name = info.get("dbname", "postgres")
            db_user = info.get("user", "postgres")
            result.backup_path = _take_backup(container, db_user, db_name, backup_dir)

            for path in pending:
                _apply_one(conn, path)
                result.applied.append(migration_version(path))
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
    finally:
        conn.close()
    return result


def _apply_one(conn: psycopg.Connection, path: Path) -> None:
    sql_text = path.read_text(encoding="utf-8")
    before = _propositions_count(conn)
    conn.execute("BEGIN")
    try:
        conn.execute(sql_text)
        after = _propositions_count(conn)
        if after < before and not allows_propositions_change(sql_text):
            raise MigrationError(
                f"{path.name} would shrink propositions from {before} to {after}; "
                "add '-- allow-propositions-change' as its first line if that is really intended"
            )
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s)",
            (migration_version(path),),
        )
        conn.execute("COMMIT")
    except MigrationError:
        conn.execute("ROLLBACK")
        raise  # already says exactly what went wrong (e.g. the shrink check)
    except Exception as exc:
        conn.execute("ROLLBACK")
        raise MigrationError(f"{path.name} failed and was rolled back; nothing from it was kept ({exc})") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--container", default="financial_rag_postgres")
    parser.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    args = parser.parse_args()

    result = run_migrations(args.dsn, args.migrations_dir, args.backup_dir, args.container)
    if result.backup_path:
        print(f"backup: {result.backup_path}")
    print(f"applied: {result.applied or '(none)'}")
    print(f"already applied: {result.skipped_already_applied}")


if __name__ == "__main__":
    main()
