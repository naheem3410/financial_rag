"""infra/versioning.py against the real test database. Step 0.4."""
from __future__ import annotations

import psycopg
import pytest

from infra.versioning import VersionConflict, optimistic_update


@pytest.fixture
def conn(clean_postgres):
    connection = psycopg.connect(clean_postgres, autocommit=True)
    connection.execute("DROP TABLE IF EXISTS versioning_smoke_test")
    connection.execute(
        "CREATE TABLE versioning_smoke_test (id TEXT PRIMARY KEY, title TEXT, version INT NOT NULL DEFAULT 1)"
    )
    connection.execute("INSERT INTO versioning_smoke_test (id, title) VALUES ('doc-1', 'Old title')")
    yield connection
    connection.execute("DROP TABLE IF EXISTS versioning_smoke_test")
    connection.close()


def test_update_with_the_current_version_succeeds(conn):
    optimistic_update(
        conn, "versioning_smoke_test", "id", "doc-1", expected_version=1,
        set_sql="title = %(title)s", set_params={"title": "New title"},
    )

    row = conn.execute("SELECT title, version FROM versioning_smoke_test WHERE id = 'doc-1'").fetchone()
    assert row == ("New title", 2)


def test_update_with_a_stale_version_is_refused(conn):
    optimistic_update(
        conn, "versioning_smoke_test", "id", "doc-1", expected_version=1,
        set_sql="title = %(title)s", set_params={"title": "First edit"},
    )  # now at version 2

    with pytest.raises(VersionConflict):
        optimistic_update(
            conn, "versioning_smoke_test", "id", "doc-1", expected_version=1,  # stale -- someone else already moved it to 2
            set_sql="title = %(title)s", set_params={"title": "Second edit, based on stale data"},
        )

    row = conn.execute("SELECT title, version FROM versioning_smoke_test WHERE id = 'doc-1'").fetchone()
    assert row == ("First edit", 2)  # the stale edit never applied


def test_update_of_a_row_that_does_not_exist_is_refused(conn):
    with pytest.raises(VersionConflict):
        optimistic_update(
            conn, "versioning_smoke_test", "id", "no-such-doc", expected_version=1,
            set_sql="title = %(title)s", set_params={"title": "New title"},
        )


def test_two_edits_at_once_only_one_wins(conn):
    """Simulates the "two edits at once on one entity" scenario: both start
    from version 1, only the first to commit succeeds."""
    optimistic_update(
        conn, "versioning_smoke_test", "id", "doc-1", expected_version=1,
        set_sql="title = %(title)s", set_params={"title": "Editor A wins"},
    )

    with pytest.raises(VersionConflict):
        optimistic_update(
            conn, "versioning_smoke_test", "id", "doc-1", expected_version=1,
            set_sql="title = %(title)s", set_params={"title": "Editor B, too late"},
        )

    row = conn.execute("SELECT title FROM versioning_smoke_test WHERE id = 'doc-1'").fetchone()
    assert row == ("Editor A wins",)
