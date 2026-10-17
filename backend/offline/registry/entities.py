"""The one shared company list (step 2.1; known issues 4 and 9, decision 10).

`PostgresMetadataStore` keeps the `entities` table current as a side effect
of every fact it writes, so a new company shows up here from ingestion
alone -- nothing else has to build it. This module turns that list into
the placeholder registry the query pipeline needs; the API, and anything
else that resolves company names, calls `build_registry` here instead of
keeping a second copy of this logic.

`offline/registry/placeholder_builder.py` is a different, lower-level tool
(dedup by ticker/CIK over already-known surface forms, plus embedding-based
collision detection) and is not wired into the live pipeline -- it is not
this module's job to replace it, only the two duplicate "build the company
placeholder list" implementations that used to exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import psycopg

from contracts.models import CanonicalIdentifiers, PlaceholderRegistryEntry
from infra.versioning import optimistic_update

_CORPORATE_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "ltd", "llc", "plc", "group", "holdings",
}


@dataclass(frozen=True)
class Entity:
    company: str
    ticker: Optional[str]
    version: int


def list_entities(conn: psycopg.Connection) -> list[Entity]:
    rows = conn.execute("SELECT company, ticker, version FROM entities ORDER BY company").fetchall()
    return [Entity(company=r[0], ticker=r[1], version=r[2]) for r in rows]


def update_entity_ticker(conn: psycopg.Connection, company: str, ticker: Optional[str], expected_version: int) -> None:
    """A rare, manual correction -- guarded by a version number (step 0.4)
    so two edits at once can't silently clobber each other; the second one
    is refused and told to reload and try again."""
    optimistic_update(
        conn, "entities", "company", company, expected_version,
        set_sql="ticker = %(ticker)s", set_params={"ticker": ticker},
    )


def _name_variants(company: str) -> set[str]:
    """A short form (strip the corporate suffix, keep the first word), and
    the possessive of each -- but never anything longer than the full name,
    since the remapper treats the longest alias as the real company name."""
    words = company.replace(",", "").split()
    trimmed = list(words)
    while trimmed and trimmed[-1].lower().strip(".") in _CORPORATE_SUFFIXES:
        trimmed.pop()
    variants = {company}
    if trimmed:
        variants.add(" ".join(trimmed))
        variants.add(trimmed[0])
    with_possessive = variants | {f"{v}'s" for v in variants}
    return {v for v in with_possessive if len(v) <= len(company)}


def build_registry(entities: Sequence[Entity]) -> list[PlaceholderRegistryEntry]:
    """One placeholder per company. A short name shared by two companies is
    dropped rather than guessed at."""
    per_company = {e.company: _name_variants(e.company) for e in entities}
    seen: dict[str, int] = {}
    for variants in per_company.values():
        for v in variants:
            seen[v] = seen.get(v, 0) + 1

    tickers = {e.company: e.ticker for e in entities}
    result: list[PlaceholderRegistryEntry] = []
    for i, company in enumerate(sorted(per_company), start=1):
        canonical = CanonicalIdentifiers(ticker=tickers[company], cik=None)
        for variant in sorted(per_company[company]):
            if seen[variant] == 1 or variant == company:
                result.append(PlaceholderRegistryEntry(surface_form=variant, placeholder=f"<ENT_{i}>", canonical=canonical))
    return result
