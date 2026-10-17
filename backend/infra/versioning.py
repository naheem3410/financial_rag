"""Optimistic concurrency for rare row edits (document card, dictionary,
entities): an update only takes effect if the version number you read is
still the current one, otherwise someone else changed it first.
"""
from __future__ import annotations

import psycopg


class VersionConflict(RuntimeError):
    """Someone else changed this row since it was last read."""


def optimistic_update(
    conn: psycopg.Connection,
    table: str,
    id_column: str,
    id_value,
    expected_version: int,
    set_sql: str,
    set_params: dict,
    version_column: str = "version",
) -> None:
    """`set_sql` is the SET clause body, e.g. "title = %(title)s". The version
    column is bumped by one automatically; callers never set it themselves.
    `table`/`id_column`/`version_column` go straight into the SQL text, so
    only ever pass fixed names from our own code, never anything typed by
    a person."""
    row = conn.execute(
        f"""
        UPDATE {table}
        SET {set_sql}, {version_column} = {version_column} + 1
        WHERE {id_column} = %(id_value)s AND {version_column} = %(expected_version)s
        RETURNING {version_column}
        """,
        {**set_params, "id_value": id_value, "expected_version": expected_version},
    ).fetchone()
    if row is None:
        raise VersionConflict(
            f"{table} row {id_value!r} was changed by someone else; reload it and try again"
        )
