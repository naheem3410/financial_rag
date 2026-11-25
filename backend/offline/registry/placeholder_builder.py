"""Placeholder registry builder — Path A & B convergence. Deterministic
placeholder assignment plus embedding-based collision-candidate
clustering. No generation. Scope: tickers, CUSIP/ISIN/CIK, accession
numbers, and entity names colliding across senses.

find_collision_candidates uses the same shared Embedder as the index
writers, and calls encode_passages (not encode_queries) — surface forms
are corpus-side data being compared to each other, not a query.

Not the live pipeline's registry anymore (step 2.1, known issues 4/9): the
API and anything else resolving company names now calls
offline/registry/entities.py's build_registry, fed by the one shared
`entities` table ingestion keeps current. This module's dedup-by-ticker/CIK
and collision-candidate detection are still real, separate tools -- kept
for that, not duplicated there.
"""
from __future__ import annotations

from typing import Any, Sequence

from contracts.interfaces import Embedder
from contracts.models import CanonicalIdentifiers, PlaceholderRegistryEntry
from contracts.vectors import cosine_similarity


def _canonical_key(entity: dict[str, Any]) -> tuple:
    ticker, cik = entity.get("ticker"), entity.get("cik")
    return (ticker, cik) if (ticker or cik) else ("surface", entity["surface_form"])


def build_registry(entities: Sequence[dict[str, Any]]) -> list[PlaceholderRegistryEntry]:
    """One placeholder per distinct canonical entity, deduped by
    (ticker, cik) or surface_form. Every surface form variant shares the
    same <ENT_N> token. Assignment order is sorted, not input order, so
    output is reproducible regardless of ingestion order."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for entity in entities:
        groups.setdefault(_canonical_key(entity), []).append(entity)

    ordered_keys = sorted(groups.keys(), key=lambda key: min(e["surface_form"] for e in groups[key]))

    entries: list[PlaceholderRegistryEntry] = []
    for i, key in enumerate(ordered_keys, start=1):
        placeholder = f"<ENT_{i}>"
        group = groups[key]
        canonical = CanonicalIdentifiers(
            ticker=next((e.get("ticker") for e in group if e.get("ticker")), None),
            cik=next((e.get("cik") for e in group if e.get("cik")), None),
        )
        seen_surface_forms: set[str] = set()
        for entity in sorted(group, key=lambda e: e["surface_form"]):
            surface_form = entity["surface_form"]
            if surface_form in seen_surface_forms:
                continue
            seen_surface_forms.add(surface_form)
            entries.append(
                PlaceholderRegistryEntry(surface_form=surface_form, placeholder=placeholder, canonical=canonical)
            )
    return entries


def find_collision_candidates(
    entries: Sequence[PlaceholderRegistryEntry],
    embedder: Embedder,
    similarity_threshold: float = 0.85,
) -> list[tuple[str, str]]:
    """Returns (placeholder_a, placeholder_b) pairs whose surface forms are
    similar but refer to distinct entities — collision candidates for the
    query analyzer. Same-entity variants are never flagged; that's dedup."""
    if len(entries) < 2:
        return []

    vectors = embedder.encode_passages([e.surface_form for e in entries])

    pairs: list[tuple[str, str]] = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            if entries[i].canonical == entries[j].canonical:
                continue
            similarity = cosine_similarity(vectors[i], vectors[j])
            if similarity >= similarity_threshold:
                pairs.append((entries[i].placeholder, entries[j].placeholder))
    return pairs
