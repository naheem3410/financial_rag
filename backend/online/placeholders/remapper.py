"""Placeholder remapper -- query-side remap + reverse-map. No model
involved.

Swaps real entity names for opaque tokens before any LLM sees the query,
and reverses it on the final answer -- so no NPCE-on call site ever has two
confusable company names in front of it at once.

Registry-first (exact surface-form match against the offline-built
registry, the production path), heuristic fallback ("ends in Corp/Inc/
Company/...") when nothing registered covers it.

`ctx.placeholder_map` always stores the longest registered alias for a
placeholder, since the metadata store is keyed by full registrant name --
so "3M" still resolves to "3M Company" for retrieval and in the final answer.

All state lives on the caller's `RequestContext.placeholder_map`, this
module holds nothing between calls.
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
    """placeholder -> longest surface form sharing it."""
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
    """Registry hit uses its fixed token, recording the canonical
    longest-alias form on first use. Otherwise reuses an earlier-assigned
    placeholder for that exact surface, or mints a fresh one that avoids
    every token in use or reserved by the registry."""
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
    """Substitutes every entity surface form in `query` with its placeholder
    token. Registry entries matched longest-first so a shorter one can't
    shadow a longer match; anything left tries the heuristic."""
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
    """Same substitution but for an already-isolated entity name, not a
    sentence -- the whole string is the surface form."""
    registry_by_surface = {e.surface_form: e.placeholder for e in registry}
    canonical_by_placeholder = _canonical_surface_forms(registry)
    return _placeholder_for(entity, ctx, registry_by_surface, canonical_by_placeholder)


def reverse_map(text: str, ctx: RequestContext) -> str:
    """Restores placeholders in `text` to real names, via the map built up
    by `remap_query`/`remap_entity` earlier in this request."""
    result = text
    for placeholder, surface in ctx.placeholder_map.items():
        result = result.replace(placeholder, surface)
    return result
