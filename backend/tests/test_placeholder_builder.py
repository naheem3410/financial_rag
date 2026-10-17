"""Placeholder registry builder tests."""
from __future__ import annotations

from offline.registry.placeholder_builder import build_registry, find_collision_candidates
from doubles import FakeEmbedder


def test_one_placeholder_per_distinct_canonical_entity():
    entities = [
        {"surface_form": "Acme Corp", "ticker": "ACME"},
        {"surface_form": "Beta Inc", "ticker": "BETA"},
    ]

    entries = build_registry(entities)

    assert len(entries) == 2
    assert {e.placeholder for e in entries} == {"<ENT_1>", "<ENT_2>"}


def test_surface_form_variants_share_one_placeholder():
    entities = [
        {"surface_form": "Acme Corp", "ticker": "ACME"},
        {"surface_form": "Acme Corporation", "ticker": "ACME"},
    ]

    entries = build_registry(entities)

    assert len(entries) == 2
    assert len({e.placeholder for e in entries}) == 1
    assert {e.surface_form for e in entries} == {"Acme Corp", "Acme Corporation"}


def test_placeholder_assignment_is_deterministic_regardless_of_input_order():
    entities_a = [
        {"surface_form": "Acme Corp", "ticker": "ACME"},
        {"surface_form": "Beta Inc", "ticker": "BETA"},
    ]
    entities_b = list(reversed(entities_a))

    result_a = {(e.surface_form, e.placeholder) for e in build_registry(entities_a)}
    result_b = {(e.surface_form, e.placeholder) for e in build_registry(entities_b)}

    assert result_a == result_b


def test_entities_without_identifiers_fall_back_to_surface_form_dedup():
    entities = [{"surface_form": "Acme Corp"}, {"surface_form": "Acme Corp"}]

    entries = build_registry(entities)

    assert len(entries) == 1


def test_canonical_identifiers_are_preserved():
    entries = build_registry([{"surface_form": "Acme Corp", "ticker": "ACME", "cik": "0000012345"}])

    assert entries[0].canonical.ticker == "ACME"
    assert entries[0].canonical.cik == "0000012345"


def test_find_collision_candidates_flags_similar_distinct_entities():
    entries = build_registry(
        [
            {"surface_form": "Acme Corp", "ticker": "ACME"},
            {"surface_form": "Acme Capital", "ticker": "ACAP"},
        ]
    )
    embedder = FakeEmbedder(
        dimension=2,
        vectors={"Acme Corp": [1.0, 0.0], "Acme Capital": [0.99, 0.14]},  # nearly parallel
    )

    pairs = find_collision_candidates(entries, embedder, similarity_threshold=0.9)

    assert len(pairs) == 1
    assert set(pairs[0]) == {"<ENT_1>", "<ENT_2>"}


def test_find_collision_candidates_does_not_flag_dissimilar_entities():
    entries = build_registry(
        [
            {"surface_form": "Acme Corp", "ticker": "ACME"},
            {"surface_form": "Beta Inc", "ticker": "BETA"},
        ]
    )
    embedder = FakeEmbedder(
        dimension=2,
        vectors={"Acme Corp": [1.0, 0.0], "Beta Inc": [0.0, 1.0]},  # orthogonal
    )

    pairs = find_collision_candidates(entries, embedder, similarity_threshold=0.9)

    assert pairs == []


def test_find_collision_candidates_never_flags_the_same_canonical_entity():
    """Surface-form variants of one entity look like a collision by similarity
    but are the same entity - dedup, not collision, must never be flagged."""
    entries = build_registry(
        [
            {"surface_form": "Acme Corp", "ticker": "ACME"},
            {"surface_form": "Acme Corporation", "ticker": "ACME"},
        ]
    )
    embedder = FakeEmbedder(
        dimension=2,
        vectors={"Acme Corp": [1.0, 0.0], "Acme Corporation": [1.0, 0.0]},  # identical
    )

    pairs = find_collision_candidates(entries, embedder, similarity_threshold=0.5)

    assert pairs == []


def test_find_collision_candidates_uses_passage_encoding_not_query_encoding():
    entries = build_registry(
        [
            {"surface_form": "Acme Corp", "ticker": "ACME"},
            {"surface_form": "Beta Inc", "ticker": "BETA"},
        ]
    )
    embedder = FakeEmbedder(dimension=8)

    find_collision_candidates(entries, embedder, similarity_threshold=2.0)  # unreachable threshold

    assert embedder.query_calls == []
    assert set(embedder.passage_calls) == {"Acme Corp", "Beta Inc"}
