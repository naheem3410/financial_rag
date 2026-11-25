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

# Below this many metric-filtered hits, a match is "lone" and gets unioned
# with the wider fallback rather than trusted on its own (decisions 26/27).
# 2 is the line: a genuine metric label almost always recurs (current vs.
# prior year, a note plus a statement line, etc.), so two-or-more already
# looks like real, cross-checked support. Exactly one hit has no such
# corroboration and is exactly the shape of a single bad or edge-case label.
_MIN_TRUSTED_METRIC_MATCHES = 2


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
        if len(with_metric) >= _MIN_TRUSTED_METRIC_MATCHES:
            return with_metric
        if with_metric:
            # A real metric usually shows up more than once in a filing (this
            # year, prior year, a note, a statement). Exactly one hit is the
            # suspicious case -- it is as likely to be a single bad label as a
            # real lone match (this is what hid General Mills' receivables
            # behind one mislabelled proposition, decisions 26/27). Don't
            # trust it alone: union it with the wider pool so it stays in the
            # race for reranking without being able to block the real
            # evidence if it is in fact wrong.
            wide = metadata_store.query(
                company=company, fiscal_year=fiscal_year, quarter=quarter, filing_type=filing_type
            )
            seen = {p.proposition_id for p in with_metric}
            return list(with_metric) + [p for p in wide if p.proposition_id not in seen]
        # zero matches: metric label missing/wrong -- company+year is still better than nothing

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
    unrelated facts) shrinks to the ones actually about that metric.
    - Zero metric-filtered matches: falls back to company+year alone rather
      than a silent empty answer (unlabelled fact, or wrong metric name).
    - Two or more: trusted and returned narrowed, as a real metric usually
      recurs across a filing -- this is the common, working case.
    - Exactly one: not trusted alone (decisions 26/27 -- a single bad label
      can permanently hide the real evidence behind it). Unioned with the
      wider company+year pool instead of replacing it, so the lone match
      stays in the running for reranking without being able to block
      anything.
    If even that comes back too thin, "try again with other names"
    (step 4.5, callers use `retry_with_other_names.filter_with_retry`
    instead of this function directly) can retry once more with another
    known name for the same company."""
    company = resolve_company(sub_query, ctx)
    return query_for_company(sub_query, company, metadata_store, enable_metric_filter)
