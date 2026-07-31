"""Placeholder registry builder — Path A & B convergence.

Deterministic placeholder assignment plus embedding-based collision-
candidate similarity clustering. No generation. Domain scope: tickers,
CUSIP/ISIN/CIK, filing accession numbers, and entity names colliding
across senses (a company name that is also a common word; parent vs
subsidiary sharing a stem).

`find_collision_candidates` takes the same shared `Embedder`
(contracts/interfaces.py) that the index writers use — see
offline/embedding/bge_embedder.py for why exactly one embedding path is
used across the whole system — and calls `encode_passages`, never
`encode_queries`: surface forms are corpus-side reference data being
compared to each other, not a query being matched against a corpus.
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
    """Deterministic placeholder assignment.

    One placeholder token per distinct canonical entity, deduped by
    `(ticker, cik)` when known, else by `surface_form`. Every distinct
    surface form seen for that entity gets its own registry entry sharing
    that placeholder — "Acme Corp" and "Acme Corporation" both resolve to
    the same `<ENT_N>` token, rather than one variant being silently
    dropped. Placeholders are assigned in an input-order-independent
    sequence (sorted by each group's earliest surface form) so registry
    contents are reproducible across runs regardless of ingestion order.
    """
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
    """Embedding-based similarity clustering. Returns
    `(placeholder_a, placeholder_b)` pairs whose surface forms are
    semantically similar but refer to distinct canonical entities —
    collision candidates for the query analyzer to consult. Entries already
    sharing a canonical identity (surface-form variants of the same entity,
    e.g. "Acme Corp" / "Acme Corporation") are never flagged against each
    other: that's deduplication, not a collision.
    """
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
