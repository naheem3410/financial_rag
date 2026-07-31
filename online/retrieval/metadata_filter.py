"""Metadata filter — metadata router/filter.

SQL over Postgres/JSONB, no model involved. Converts a sub-query's slots
into a hard filter on company, fiscal period, and filing type — prevents
retrieving the right metric from the wrong period or issuer, the most
common financial retrieval error.

By the time a sub-query reaches this stage it has already been through the
query-side placeholder remapper: `sub_query.entity` holds a placeholder
token (e.g. `"<ENT_1>"`), not a real company name, while the persisted
metadata store indexes propositions under their real company name —
placeholder substitution is an online, per-request transformation, never
baked into the offline index. This filter resolves the placeholder back to
a real name via the per-request `RequestContext.placeholder_map` before
querying. If the entity can't be resolved (not in the map — e.g. NPCE-off
passthrough, where `sub_query.entity` holds the raw query text, not a
placeholder), the filter applies no company constraint rather than
querying for a company name that can't exist, which would silently return
nothing for the wrong reason.
"""
from __future__ import annotations

from typing import Sequence

from contracts.models import Proposition, RequestContext, SubQuery
from offline.indexing.metadata_store_postgres import PostgresMetadataStore

_PLACEHOLDER_RE_PREFIX = "<ENT_"


def filter_candidates(
    sub_query: SubQuery, ctx: RequestContext, metadata_store: PostgresMetadataStore
) -> Sequence[Proposition]:
    """Returns the metadata-filtered candidate set for one sub-query, ready
    for the hybrid retriever to rank."""
    company = ctx.placeholder_map.get(sub_query.entity)
    if company is None and not sub_query.entity.startswith(_PLACEHOLDER_RE_PREFIX):
        # Not a placeholder at all (e.g. NPCE-off passthrough) — no company
        # constraint, rather than filtering on raw query text as if it were
        # a company name.
        company = None
    elif company is None:
        # A placeholder token with no registry entry — fail closed: filter
        # on the literal (unresolvable) token so this returns nothing,
        # rather than silently dropping the company constraint.
        company = sub_query.entity

    fiscal_year = sub_query.period.fiscal_year or None  # 0 is the "unset" sentinel
    quarter = sub_query.period.quarter

    filing_type = sub_query.filing_types[0] if sub_query.filing_types else None

    return metadata_store.query(
        company=company, fiscal_year=fiscal_year, quarter=quarter, filing_type=filing_type
    )
