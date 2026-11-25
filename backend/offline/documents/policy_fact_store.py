"""Persistent store for PolicyFact rows -- the non-finance counterpart to
PostgresMetadataStore, kept separate since the two fact shapes are
genuinely different (a numeric proposition vs. a whole rule block).
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.models import PolicyFact
from infra.connections import connect_postgres

_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_facts (
    fact_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policy_facts_document ON policy_facts (document_id);
"""


class PolicyFactStore:
    """One connection per instance, same convention as PostgresMetadataStore."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._conn = connect_postgres(dsn, row_factory=dict_row, autocommit=True)
        self._conn.execute(_SCHEMA)

    def upsert(self, fact: PolicyFact) -> None:
        self._conn.execute(
            """
            INSERT INTO policy_facts (fact_id, document_id, data)
            VALUES (%(fact_id)s, %(document_id)s, %(data)s)
            ON CONFLICT (fact_id) DO UPDATE SET document_id = EXCLUDED.document_id, data = EXCLUDED.data
            """,
            {
                "fact_id": fact.fact_id, "document_id": fact.document_id,
                "data": Jsonb(fact.model_dump(mode="json")),
            },
        )

    def get(self, fact_id: str) -> PolicyFact:
        row = self._conn.execute("SELECT data FROM policy_facts WHERE fact_id = %s", (fact_id,)).fetchone()
        if row is None:
            raise KeyError(fact_id)
        return PolicyFact.model_validate(row["data"])

    def ids(self) -> set[str]:
        rows = self._conn.execute("SELECT fact_id FROM policy_facts").fetchall()
        return {row["fact_id"] for row in rows}

    def query(self, document_id: str) -> list[PolicyFact]:
        rows = self._conn.execute(
            "SELECT data FROM policy_facts WHERE document_id = %s ORDER BY fact_id", (document_id,)
        ).fetchall()
        return [PolicyFact.model_validate(row["data"]) for row in rows]

    def delete_many(self, fact_ids) -> None:
        if not fact_ids:
            return
        self._conn.execute("DELETE FROM policy_facts WHERE fact_id = ANY(%s)", (list(fact_ids),))

    def close(self) -> None:
        self._conn.close()
