"""Persistent metadata store — PostgreSQL/JSONB.

No model involved. The metadata filter is a relational query ("company = X
AND fiscal_year = 2024 AND quarter IN (3,4)"), so the common filter columns
are indexed directly; the full proposition record is also stored as JSONB
so no field is lost and no migration is needed to add one later.

Why persistence matters here specifically: proposition segmentation is an
LLM call. If the offline index were rebuilt between experiment cells, each
cell could end up with a different proposition set — a different retrieval
core per cell, which confounds every measured NPCE/CoVe delta with
retrieval noise rather than a real effect. This store is built once
(offline/indexing/build.py) and reused unchanged across C0-C4.

Backed by a project-scoped `postgres:16.4` container (pinned tag) — this is
infrastructure, not an LLM call site, so it's exempt from
`tests/test_no_direct_sdk_imports.py`'s scan by construction (that scan
targets LLM provider SDKs only).
"""
from __future__ import annotations

from typing import Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.models import Proposition

def _strip_null_bytes(value):
    """Defensive belt-and-suspenders: `offline/parsers/layout_parser.py`
    strips U+0000 at PDF extraction time (its own docstring has the full
    story — a font-decoding artifact, not real content), but propositions
    persisted via a checkpoint sidecar written *before* that fix existed
    can still carry the byte, and PostgreSQL's JSONB type hard-rejects it
    (`UntranslatableCharacter`) regardless of where it came from. Recurses
    through the `model_dump(mode="json")` structure since the byte can be
    nested inside `text` or any other string field.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _strip_null_bytes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_null_bytes(v) for v in value]
    return value


_SCHEMA = """
CREATE TABLE IF NOT EXISTS propositions (
    proposition_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    fiscal_year INTEGER,
    quarter INTEGER,
    filing_type TEXT NOT NULL,
    page INTEGER NOT NULL,
    source_uri TEXT NOT NULL,
    data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_propositions_company_period
    ON propositions (company, fiscal_year, quarter);
CREATE INDEX IF NOT EXISTS idx_propositions_filing_type
    ON propositions (filing_type);
"""


class PostgresMetadataStore:
    """Metadata store. One connection per instance; construct one per
    build/load run (offline/indexing/build.py), not per query."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
        self._conn.execute(_SCHEMA)

    def upsert(self, proposition: Proposition) -> None:
        period = proposition.numeric.period if proposition.numeric else None
        self._conn.execute(
            """
            INSERT INTO propositions
                (proposition_id, company, fiscal_year, quarter, filing_type, page, source_uri, data)
            VALUES (%(proposition_id)s, %(company)s, %(fiscal_year)s, %(quarter)s,
                    %(filing_type)s, %(page)s, %(source_uri)s, %(data)s)
            ON CONFLICT (proposition_id) DO UPDATE SET
                company = EXCLUDED.company,
                fiscal_year = EXCLUDED.fiscal_year,
                quarter = EXCLUDED.quarter,
                filing_type = EXCLUDED.filing_type,
                page = EXCLUDED.page,
                source_uri = EXCLUDED.source_uri,
                data = EXCLUDED.data
            """,
            {
                "proposition_id": proposition.proposition_id,
                "company": proposition.metadata.company,
                "fiscal_year": period.fiscal_year if period else None,
                "quarter": period.quarter if period else None,
                "filing_type": proposition.metadata.filing_type,
                "page": proposition.metadata.page,
                "source_uri": proposition.metadata.source_uri,
                "data": Jsonb(_strip_null_bytes(proposition.model_dump(mode="json"))),
            },
        )

    def ids(self) -> set[str]:
        rows = self._conn.execute("SELECT proposition_id FROM propositions").fetchall()
        return {row["proposition_id"] for row in rows}

    def get(self, proposition_id: str) -> Proposition:
        row = self._conn.execute(
            "SELECT data FROM propositions WHERE proposition_id = %s", (proposition_id,)
        ).fetchone()
        if row is None:
            raise KeyError(proposition_id)
        return Proposition.model_validate(row["data"])

    def all(self) -> list[Proposition]:
        # ORDER BY matters here: without it, row order is whatever
        # Postgres's query planner happens to produce for this scan (stable
        # in casual practice, never a guarantee -- can shift with VACUUM,
        # index usage, or a plan change), and every downstream consumer
        # that cares about order (online/retrieval/hybrid.py, online/
        # retrieval/fusion.py's stable sort uses input order to break ties)
        # would silently inherit that non-determinism. `proposition_id` is
        # an arbitrary but fixed, always-present tiebreaker -- not
        # semantically meaningful, just deterministic.
        rows = self._conn.execute("SELECT data FROM propositions ORDER BY proposition_id").fetchall()
        return [Proposition.model_validate(row["data"]) for row in rows]

    def query(
        self,
        company: Optional[str] = None,
        fiscal_year: Optional[int] = None,
        quarter: Optional[int] = None,
        filing_type: Optional[str] = None,
    ) -> list[Proposition]:
        """Exact-match filtering over the indexed columns — what the
        metadata filter needs: `company = X AND fiscal_year = 2024 AND
        quarter = 3`, etc. `ORDER BY proposition_id` for the same
        determinism reason as `all()` above."""
        clauses, params = [], {}
        if company is not None:
            clauses.append("company = %(company)s")
            params["company"] = company
        if fiscal_year is not None:
            clauses.append("fiscal_year = %(fiscal_year)s")
            params["fiscal_year"] = fiscal_year
        if quarter is not None:
            clauses.append("quarter = %(quarter)s")
            params["quarter"] = quarter
        if filing_type is not None:
            clauses.append("filing_type = %(filing_type)s")
            params["filing_type"] = filing_type
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT data FROM propositions {where} ORDER BY proposition_id", params
        ).fetchall()
        return [Proposition.model_validate(row["data"]) for row in rows]

    def clear(self) -> None:
        """Test/reset helper — truncates the table. Not part of the normal
        write path."""
        self._conn.execute("TRUNCATE TABLE propositions")

    def close(self) -> None:
        self._conn.close()
