"""Try again with other names (step 4.5, decision 5 and 11; widened by
decision 31): try every other known name for the same company and union
whatever any of them finds in with the first name's result, before giving
an honest "not found" instead of a silently weak answer.

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
    when they want the retry road.

    A company that renamed mid-corpus (decision 22's manual alias groups,
    e.g. Adobe) has real filings filed under two different legal names. The
    one name `resolve_company` picks (the longest alias) governs one era,
    not the other -- a query for a period that falls under the OTHER name
    can come back non-empty (a stray same-year mention in the right-named
    filing) without ever containing the real evidence, which then silently
    blocked the retry that would have found it (decision 31). So every
    other known surface form is queried and unioned in, not just tried
    when the first name comes back empty.

    Decision 11's "one retry only" cap doesn't apply any more: these are
    local Postgres lookups, not an LLM call, so there is no cost reason to
    cap them, and decision 31 found that capping at exactly one (picking
    the single longest alternate) can pick a synthetic possessive variant
    ("Adobe Systems's") over the one alternate that is a real legal name on
    file ("Adobe Inc.") -- length alone doesn't reliably sort "real other
    name" ahead of "short-form/possessive reachability alias". Querying
    every alternate is still cheap for every other company too: with only
    one real legal name on file, the alternate surface forms are short-name
    variants that match nothing in the metadata store, so this changes
    nothing for them."""
    company = resolve_company(sub_query, ctx)
    first = query_for_company(sub_query, company, metadata_store, enable_metric_filter)

    if company is None:
        # NPCE-off passthrough -- nothing was resolved to retry with either.
        return RetryOutcome(candidates=tuple(first), retried=False, names_tried=(), found=bool(first))

    alternates = other_surface_forms(sub_query.entity, registry, company)
    if not alternates:
        return RetryOutcome(candidates=tuple(first), retried=False, names_tried=(), found=bool(first))

    seen = {p.proposition_id for p in first}
    merged = list(first)
    for alternate_name in alternates:
        retried_candidates = query_for_company(sub_query, alternate_name, metadata_store, enable_metric_filter)
        merged.extend(p for p in retried_candidates if p.proposition_id not in seen)
        seen.update(p.proposition_id for p in retried_candidates)
    return RetryOutcome(
        candidates=tuple(merged),
        retried=True,
        names_tried=tuple(alternates),  # every alternate actually queried, found or not
        found=bool(merged),
    )
