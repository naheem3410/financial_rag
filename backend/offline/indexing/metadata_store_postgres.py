"""Persistent metadata store — PostgreSQL/JSONB.

Filter columns (company, fiscal_year, quarter, ...) are indexed directly for
the relational filter query; the full record also lives in JSONB so nothing
gets lost and no migration is needed to add a field later. Built once via
offline/indexing/build.py and reused across C0-C4 — segmentation is an LLM
call, so rebuilding between cells would give each cell a different core.
"""
from __future__ import annotations

from typing import Optional, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.models import Proposition
from infra.connections import connect_postgres

def _strip_null_bytes(value):
    """Belt-and-suspenders: layout_parser strips U+0000 at extraction time,
    but old checkpoint sidecars can still carry it and JSONB hard-rejects
    it. Recurses since the byte can be nested in any string field."""
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
    data JSONB NOT NULL,
    metrics TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_propositions_company_period
    ON propositions (company, fiscal_year, quarter);
CREATE INDEX IF NOT EXISTS idx_propositions_filing_type
    ON propositions (filing_type);
"""
# A table created before step 1.4 has no `metrics` column yet -- `CREATE TABLE
# IF NOT EXISTS` above is a no-op for it, so add it the same way the formal
# migration (db/migrations/0001_add_metrics_column.sql) does. Safe to run on
# every connect: both statements are no-ops once already applied. Also
# covers a brand-new table, since the GIN index isn't in _SCHEMA -- this is
# the one place it's created, always after the column is guaranteed to exist.
_ADD_METRICS_COLUMN_IF_MISSING = """
ALTER TABLE propositions ADD COLUMN IF NOT EXISTS metrics TEXT[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_propositions_metrics ON propositions USING GIN (metrics);
"""

# Step 2.1: the one shared company list. Every upsert keeps it current, so a
# new company shows up from ingestion alone -- nothing else has to build it.
_ENTITIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    company TEXT PRIMARY KEY,
    ticker TEXT,
    version INT NOT NULL DEFAULT 1
)
"""


class PostgresMetadataStore:
    """One connection per instance — construct per build/load run, not per query."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._conn = connect_postgres(dsn, row_factory=dict_row, autocommit=True)
        self._conn.execute(_SCHEMA)
        self._conn.execute(_ADD_METRICS_COLUMN_IF_MISSING)  # heals a table made before step 1.4
        self._conn.execute(_ENTITIES_SCHEMA)
        self._backfill_entities_if_empty()  # heals facts written before step 2.1 existed

    def _backfill_entities_if_empty(self) -> None:
        """One cheap count check on every connect; the actual backfill only
        runs the one time the table is genuinely empty but facts already
        exist (a database from before step 2.1). Never overwrites a ticker
        an ordinary upsert already learned."""
        (count,) = self._conn.execute("SELECT COUNT(*) FROM entities").fetchone().values()
        if count > 0:
            return
        self._conn.execute(
            """
            INSERT INTO entities (company, ticker)
            SELECT DISTINCT ON (company) company, data->'metadata'->>'ticker'
            FROM propositions
            ORDER BY company, (data->'metadata'->>'ticker') IS NULL
            ON CONFLICT (company) DO UPDATE SET ticker = COALESCE(entities.ticker, EXCLUDED.ticker)
            """
        )

    def upsert(self, proposition: Proposition) -> None:
        period = proposition.numeric.period if proposition.numeric else None
        self._conn.execute(
            """
            INSERT INTO propositions
                (proposition_id, company, fiscal_year, quarter, filing_type, page, source_uri, data, metrics)
            VALUES (%(proposition_id)s, %(company)s, %(fiscal_year)s, %(quarter)s,
                    %(filing_type)s, %(page)s, %(source_uri)s, %(data)s, %(metrics)s)
            ON CONFLICT (proposition_id) DO UPDATE SET
                company = EXCLUDED.company,
                fiscal_year = EXCLUDED.fiscal_year,
                quarter = EXCLUDED.quarter,
                filing_type = EXCLUDED.filing_type,
                page = EXCLUDED.page,
                source_uri = EXCLUDED.source_uri,
                data = EXCLUDED.data,
                metrics = EXCLUDED.metrics
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
                "metrics": list(proposition.metrics),
            },
        )
        self._upsert_entity(proposition.metadata.company, proposition.metadata.ticker)

    def _upsert_entity(self, company: str, ticker: Optional[str]) -> None:
        """Keeps the shared company list current on every write. Never
        overwrites a known ticker with a missing one from a later fact, and
        never touches `version` -- that's only for a deliberate manual edit
        (see infra.versioning / offline.registry.entities.update_entity_ticker)."""
        self._conn.execute(
            """
            INSERT INTO entities (company, ticker) VALUES (%(company)s, %(ticker)s)
            ON CONFLICT (company) DO UPDATE SET
                ticker = COALESCE(entities.ticker, EXCLUDED.ticker)
            """,
            {"company": company, "ticker": ticker},
        )

    def entities(self):
        """The shared company list (step 2.1) -- the API, and anything else
        that needs to know which companies are indexed, reads this instead
        of scanning every proposition or keeping its own copy."""
        from offline.registry.entities import Entity

        rows = self._conn.execute("SELECT company, ticker, version FROM entities ORDER BY company").fetchall()
        return [Entity(company=r["company"], ticker=r["ticker"], version=r["version"]) for r in rows]

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
        # ORDER BY matters: without it, row order follows the query planner
        # (looks stable, isn't guaranteed), and fusion.py's tie-breaking sort
        # would silently inherit that non-determinism.
        rows = self._conn.execute("SELECT data FROM propositions ORDER BY proposition_id").fetchall()
        return [Proposition.model_validate(row["data"]) for row in rows]

    def query(
        self,
        company: Optional[str] = None,
        fiscal_year: Optional[int] = None,
        quarter: Optional[int] = None,
        filing_type: Optional[str] = None,
        metrics: Optional[Sequence[str]] = None,
    ) -> list[Proposition]:
        """Exact-match filtering over the indexed columns. `metrics` matches
        a fact labelled with *any* of the given metrics (step 1.4) -- an
        empty list is the same as leaving it out, never "match nothing".
        Ordered by proposition_id for the same determinism reason as all()
        above."""
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
        if metrics:
            clauses.append("metrics && %(metrics)s")
            params["metrics"] = list(metrics)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT data FROM propositions {where} ORDER BY proposition_id", params
        ).fetchall()
        return [Proposition.model_validate(row["data"]) for row in rows]

    def clear(self) -> None:
        """Test/reset helper — truncates the table, not part of normal writes."""
        self._conn.execute("TRUNCATE TABLE propositions")

    def close(self) -> None:
        self._conn.close()
