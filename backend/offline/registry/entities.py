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
_LEADING_ARTICLES = {"the"}

# Same real company, different legal name on file (e.g. a post-rename
# filing) -- grouped here by hand so their short form isn't dropped as a
# false collision. Narrow and manual on purpose: this does NOT generalize,
# it only covers pairs someone has actually noticed and listed. See
# KNOWN_ISSUES.md decision 22 for why, and check back there before adding
# a filing for a company that might have renamed since an earlier one.
_MANUAL_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"Adobe Systems Incorporated", "Adobe Inc."}),  # Adobe's 2018 legal-name change
)


def _group_key(company: str) -> frozenset[str]:
    for group in _MANUAL_ALIAS_GROUPS:
        if company in group:
            return group
    return frozenset({company})


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
        # "The Boeing Company" -> trimmed[0] is "The", not the real short
        # name. Drop a leading article before taking the first word, same
        # way a trailing suffix is dropped above, so "Boeing" is reachable.
        core = trimmed[1:] if len(trimmed) > 1 and trimmed[0].lower() in _LEADING_ARTICLES else trimmed
        if core:
            variants.add(" ".join(core))
            variants.add(core[0])
    with_possessive = variants | {f"{v}'s" for v in variants}
    return {v for v in with_possessive if len(v) <= len(company)}


def build_registry(entities: Sequence[Entity]) -> list[PlaceholderRegistryEntry]:
    """One placeholder per company, or per manually-aliased group of legal
    names for the same real company (`_MANUAL_ALIAS_GROUPS`). A short name
    shared by two different companies is dropped rather than guessed at;
    one shared only within a single alias group is kept, since it's the
    same company either way."""
    by_company = {e.company: e for e in entities}
    group_companies: dict[frozenset[str], set[str]] = {}
    for company in by_company:
        group_companies.setdefault(_group_key(company), set()).add(company)

    group_variants: dict[frozenset[str], set[str]] = {
        key: set().union(*(_name_variants(c) for c in companies))
        for key, companies in group_companies.items()
    }

    seen: dict[str, int] = {}
    for variants in group_variants.values():
        for v in variants:
            seen[v] = seen.get(v, 0) + 1

    result: list[PlaceholderRegistryEntry] = []
    for i, key in enumerate(sorted(group_variants, key=sorted), start=1):
        companies = group_companies[key]
        ticker = next((by_company[c].ticker for c in companies if by_company[c].ticker), None)
        canonical = CanonicalIdentifiers(ticker=ticker, cik=None)
        for variant in sorted(group_variants[key]):
            if seen[variant] == 1 or variant in companies:
                result.append(PlaceholderRegistryEntry(surface_form=variant, placeholder=f"<ENT_{i}>", canonical=canonical))
    return result
