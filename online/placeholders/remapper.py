"""Placeholder remapper — query-side remap + reverse-map. No model
involved.

Substitutes real entity surface forms for opaque placeholder tokens before
any LLM sees the query, and reverses the substitution on the final answer
text — so no LLM call site in the NPCE-on path ever has two real,
confusable company names in front of it at once (`online/output/
generator.py`'s own "no collision surface to hallucinate across" guarantee
depends on this actually happening upstream).

Registry-first, heuristic fallback: a real `PlaceholderRegistryEntry` list
(built offline, `offline/registry/placeholder_builder.py`, from the actual
indexed corpus's company names) is checked first via exact surface-form
matching — this is the production path (`online/pipeline_c3.py` passes the
real registry). Anything not covered by the registry falls back to a plain
"ends in a corporate-entity suffix" heuristic (Corp, Inc, Company, ...) so
`remap_query` still substitutes something sensible with no registry at
all; it is not meant to be the reliable mechanism for real filings.

Canonical surface form, not whichever alias matched: `build_registry`
groups every alias of one company under one placeholder ("3M" and "3M
Company" both resolve to the same `<ENT_N>`) but the persisted metadata
store (`offline/indexing/metadata_store_postgres.py`) is keyed by the full
registrant name, and the metadata filter (`online/retrieval/
metadata_filter.py`) does an exact match against that stored name via
`ctx.placeholder_map`. So `ctx.placeholder_map[placeholder]` always records
the longest surface form sharing that placeholder in the registry — a
corpus-specific convention (true for this corpus's three companies: "3M
Company" / "Activision Blizzard, Inc." / "Adobe Systems Incorporated" are
each the longest of their own registered aliases), not a general
name-normalization technique. If a query mentions the shorthand "3M", the
placeholder still resolves to "3M Company" for retrieval and for the final
reverse-mapped answer text.

Per-request, never global: all state lives on the caller's own
`RequestContext.placeholder_map` — this module holds nothing between
calls.
"""
from __future__ import annotations

import re
from typing import Sequence

from contracts.models import PlaceholderRegistryEntry, RequestContext

_ENTITY_SUFFIXES = (
    "Corporation", "Incorporated", "Corp", "Inc", "Company", "Co",
    "Ltd", "LLC", "LLP", "plc", "Group", "Holdings",
)
_HEURISTIC_ENTITY_RE = re.compile(
    r"\b([A-Z][A-Za-z&]*(?:\s+[A-Z][A-Za-z&]*)*\s+(?:" + "|".join(_ENTITY_SUFFIXES) + r"))\.?\b"
)


def _canonical_surface_forms(registry: Sequence[PlaceholderRegistryEntry]) -> dict[str, str]:
    """placeholder -> the longest surface form sharing that placeholder."""
    by_placeholder: dict[str, str] = {}
    for entry in registry:
        current = by_placeholder.get(entry.placeholder, "")
        if len(entry.surface_form) > len(current):
            by_placeholder[entry.placeholder] = entry.surface_form
    return by_placeholder


def _placeholder_for(
    surface: str,
    ctx: RequestContext,
    registry_by_surface: dict[str, str],
    canonical_by_placeholder: dict[str, str],
) -> str:
    """The placeholder for `surface`. A registry hit always uses the
    registry's own fixed token, recording the *canonical* (longest-alias)
    surface form on first use so later aliases of the same company resolve
    to the same, already-canonical entry. Anything not in the registry
    reuses a placeholder already assigned to that exact surface form earlier
    in this request, else mints a fresh one that avoids every token already
    in use or reserved by the registry (so a heuristic-minted token can
    never collide with an unused registry entry's own placeholder)."""
    if surface in registry_by_surface:
        placeholder = registry_by_surface[surface]
        if placeholder not in ctx.placeholder_map:
            ctx.placeholder_map[placeholder] = canonical_by_placeholder[placeholder]
        return placeholder

    for placeholder, real_name in ctx.placeholder_map.items():
        if real_name == surface:
            return placeholder

    reserved = set(ctx.placeholder_map.keys()) | set(registry_by_surface.values())
    n = 1
    placeholder = f"<ENT_{n}>"
    while placeholder in reserved:
        n += 1
        placeholder = f"<ENT_{n}>"
    ctx.placeholder_map[placeholder] = surface
    return placeholder


def remap_query(query: str, ctx: RequestContext, registry: Sequence[PlaceholderRegistryEntry] = ()) -> str:
    """Substitutes every entity surface form found in `query` with its
    placeholder token, recording the mapping into `ctx.placeholder_map`.
    Registry entries are matched first (longest surface form first, so one
    entry can't be partially shadowed by a shorter one); anything left is
    checked against the generic corporate-suffix heuristic."""
    registry_by_surface = {e.surface_form: e.placeholder for e in registry}
    canonical_by_placeholder = _canonical_surface_forms(registry)
    result = query

    for surface in sorted(registry_by_surface, key=len, reverse=True):
        if surface in result:
            placeholder = _placeholder_for(surface, ctx, registry_by_surface, canonical_by_placeholder)
            result = result.replace(surface, placeholder)

    def _sub(match: re.Match) -> str:
        return _placeholder_for(match.group(1), ctx, registry_by_surface, canonical_by_placeholder)

    return _HEURISTIC_ENTITY_RE.sub(_sub, result)


def remap_entity(entity: str, ctx: RequestContext, registry: Sequence[PlaceholderRegistryEntry] = ()) -> str:
    """Same substitution applied to a single, already-isolated entity name
    (e.g. a query splitter's `SubQuery.entity`, not a full sentence) — no
    sentence to scan, so the whole string is the surface form to
    substitute."""
    registry_by_surface = {e.surface_form: e.placeholder for e in registry}
    canonical_by_placeholder = _canonical_surface_forms(registry)
    return _placeholder_for(entity, ctx, registry_by_surface, canonical_by_placeholder)


def reverse_map(text: str, ctx: RequestContext) -> str:
    """Restores every placeholder token in `text` back to its real surface
    form via `ctx.placeholder_map`, built up by `remap_query`/
    `remap_entity` earlier in the same request."""
    result = text
    for placeholder, surface in ctx.placeholder_map.items():
        result = result.replace(placeholder, surface)
    return result
