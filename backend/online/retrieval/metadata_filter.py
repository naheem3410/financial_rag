"""Metadata filter: SQL over Postgres/JSONB, no model involved. Hard-filters
on company/period/filing-type so we never pull the right metric from the wrong issuer.

sub_query.entity arrives as a placeholder token (e.g. "<ENT_1>"), not a
real name — this resolves it via ctx.placeholder_map before querying,
since the offline index stores real company names.
"""
from __future__ import annotations

from typing import Optional, Sequence

from contracts.models import Proposition, RequestContext, SubQuery
from offline.indexing.metadata_store_postgres import PostgresMetadataStore

_PLACEHOLDER_RE_PREFIX = "<ENT_"


def resolve_company(sub_query: SubQuery, ctx: RequestContext) -> Optional[str]:
    """The company string to filter on, or `None` for "no constraint"
    (NPCE-off passthrough). An unresolvable placeholder fails closed on the
    literal token rather than silently dropping the constraint."""
    company = ctx.placeholder_map.get(sub_query.entity)
    if company is None and not sub_query.entity.startswith(_PLACEHOLDER_RE_PREFIX):
        return None
    if company is None:
        return sub_query.entity
    return company


def query_for_company(
    sub_query: SubQuery,
    company: Optional[str],
    metadata_store: PostgresMetadataStore,
    enable_metric_filter: bool = False,
) -> Sequence[Proposition]:
    """The actual metadata query for one already-resolved company string --
    shared by `filter_candidates` (resolves the company from the
    placeholder map) and the "try again with other names" retry (step 4.5,
    online/routing/retry_with_other_names.py), which needs to query with an
    explicit alternate name instead."""
    fiscal_year = sub_query.period.fiscal_year or None  # 0 is the "unset" sentinel
    quarter = sub_query.period.quarter

    filing_type = sub_query.filing_types[0] if sub_query.filing_types else None

    if enable_metric_filter and sub_query.metric:
        with_metric = metadata_store.query(
            company=company, fiscal_year=fiscal_year, quarter=quarter,
            filing_type=filing_type, metrics=[sub_query.metric],
        )
        if with_metric:
            return with_metric
        # fallback: metric label missing/wrong -- company+year is still better than nothing

    return metadata_store.query(
        company=company, fiscal_year=fiscal_year, quarter=quarter, filing_type=filing_type
    )


def filter_candidates(
    sub_query: SubQuery,
    ctx: RequestContext,
    metadata_store: PostgresMetadataStore,
    enable_metric_filter: bool = False,
) -> Sequence[Proposition]:
    """Returns the metadata-filtered candidates for one sub-query, ready
    for the hybrid retriever.

    `enable_metric_filter=True` (step 1.4, decision 12) also filters by the
    sub-query's metric label, so a big company+year pile (hundreds of
    unrelated facts) shrinks to the ones actually about that metric. If
    that leaves nothing -- an unlabelled fact, or the wrong metric name --
    this falls back to company+year alone rather than a silent empty
    answer. If even that comes back too thin, "try again with other names"
    (step 4.5, callers use `retry_with_other_names.filter_with_retry`
    instead of this function directly) can retry once more with another
    known name for the same company."""
    company = resolve_company(sub_query, ctx)
    return query_for_company(sub_query, company, metadata_store, enable_metric_filter)
