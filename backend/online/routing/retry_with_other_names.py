"""Try again with other names (step 4.5, decision 5 and 11): when the
metadata filter's normal exact-match lookup comes back empty, retry once --
and only once -- with another known name for the same company, before
giving an honest "not found" instead of a silently weak answer.

No model call: the alternate names tried are the same placeholder registry
(step 2.1) every other part of the query pipeline already uses, never a
guess. This replaces the interim note left in
online/retrieval/metadata_filter.py's own docstring when step 1.4 was
built: "the full try again with other names road doesn't exist yet".

Honest scope note: "weak" here means empty -- zero candidates -- the same
bar filter_candidates' own metric-filter fallback already uses ("if that
leaves nothing"). A looser bar (e.g. "fewer than N candidates") was
considered and set aside: the plan gives no specific number, and treating
a handful of real, correctly-filtered candidates as "too few" risks
retrying (and diluting the log) on cases that were never actually broken.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from contracts.models import PlaceholderRegistryEntry, Proposition, RequestContext, SubQuery
from offline.indexing.metadata_store_postgres import PostgresMetadataStore
from online.retrieval.metadata_filter import query_for_company, resolve_company


@dataclass(frozen=True)
class RetryOutcome:
    candidates: tuple[Proposition, ...]
    retried: bool
    names_tried: tuple[str, ...]  # the alternate name(s) actually queried, for "How it happened"
    found: bool  # something was returned, by the first try or the retry


def other_surface_forms(placeholder: str, registry: Sequence[PlaceholderRegistryEntry], already_tried: str) -> list[str]:
    """Other known surface forms sharing the same placeholder as the one
    already tried -- longest first, since the metadata store is keyed by
    the full registrant name (see online/placeholders/remapper.py's own
    note on why the longest alias is the canonical one)."""
    matches = {
        entry.surface_form
        for entry in registry
        if entry.placeholder == placeholder and entry.surface_form != already_tried
    }
    return sorted(matches, key=len, reverse=True)


def filter_with_retry(
    sub_query: SubQuery,
    ctx: RequestContext,
    metadata_store: PostgresMetadataStore,
    registry: Sequence[PlaceholderRegistryEntry],
    enable_metric_filter: bool = False,
) -> RetryOutcome:
    """The entry point callers use in place of `filter_candidates` directly
    when they want the retry road. One retry only (decision 11's cap) -- a
    second empty result is an honest "not found", never a third try."""
    company = resolve_company(sub_query, ctx)
    first = query_for_company(sub_query, company, metadata_store, enable_metric_filter)
    if first:
        return RetryOutcome(candidates=tuple(first), retried=False, names_tried=(), found=True)

    if company is None:
        # NPCE-off passthrough -- nothing was resolved to retry with either.
        return RetryOutcome(candidates=(), retried=False, names_tried=(), found=False)

    alternates = other_surface_forms(sub_query.entity, registry, company)
    if not alternates:
        return RetryOutcome(candidates=(), retried=False, names_tried=(), found=False)

    alternate_name = alternates[0]
    retried_candidates = query_for_company(sub_query, alternate_name, metadata_store, enable_metric_filter)
    return RetryOutcome(
        candidates=tuple(retried_candidates),
        retried=True,
        names_tried=(alternate_name,),
        found=bool(retried_candidates),
    )
