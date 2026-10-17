"""Placeholder remapper tests."""
from __future__ import annotations

from contracts.models import CanonicalIdentifiers, PlaceholderRegistryEntry, RequestContext
from online.placeholders.remapper import remap_entity, remap_query, reverse_map


def _ctx() -> RequestContext:
    return RequestContext(request_id="r_1", config_hash="cfg_abc")


def test_roundtrip_with_heuristic_fallback_no_registry():
    ctx = _ctx()
    query = "How did Acme Corp's operating margin compare to Beta Inc's?"

    remapped = remap_query(query, ctx)
    restored = reverse_map(remapped, ctx)

    assert restored == query
    assert "Acme Corp" not in remapped
    assert "Beta Inc" not in remapped


def test_registry_entry_used_over_heuristic():
    registry = [
        PlaceholderRegistryEntry(surface_form="3M Company", placeholder="<ENT_1>", canonical=CanonicalIdentifiers(ticker="MMM")),
    ]
    ctx = _ctx()
    remapped = remap_query("What was 3M Company's net income?", ctx, registry=registry)

    assert remapped == "What was <ENT_1>'s net income?"
    assert ctx.placeholder_map == {"<ENT_1>": "3M Company"}


def test_same_surface_form_reuses_same_placeholder_within_one_request():
    ctx = _ctx()
    query = "Acme Corp reported revenue. Acme Corp also reported net income."

    remapped = remap_query(query, ctx)

    assert remapped.count("<ENT_1>") == 2
    assert len(ctx.placeholder_map) == 1


def test_placeholder_map_is_per_request_not_shared():
    """Never a global. Two independent contexts must not leak placeholder
    assignments into each other."""
    ctx_a = _ctx()
    ctx_b = RequestContext(request_id="r_2", config_hash="cfg_abc")

    remap_query("Acme Corp reported revenue.", ctx_a)
    remap_query("Beta Inc reported revenue.", ctx_b)

    assert ctx_a.placeholder_map == {"<ENT_1>": "Acme Corp"}
    assert ctx_b.placeholder_map == {"<ENT_1>": "Beta Inc"}


def test_remap_entity_isolated_string():
    ctx = _ctx()
    placeholder = remap_entity("3M", ctx)

    assert placeholder == "<ENT_1>"
    assert ctx.placeholder_map == {"<ENT_1>": "3M"}
    assert reverse_map(placeholder, ctx) == "3M"


def test_remap_entity_uses_registry_and_stays_consistent_with_remap_query():
    """Same company in the query text and later as a SubQuery.entity must
    resolve to the same placeholder, or metadata_filter.py splits its facts across two tokens."""
    registry = [
        PlaceholderRegistryEntry(surface_form="3M Company", placeholder="<ENT_1>", canonical=CanonicalIdentifiers()),
    ]
    ctx = _ctx()
    remap_query("What was 3M Company's net income?", ctx, registry=registry)
    placeholder = remap_entity("3M Company", ctx, registry=registry)

    assert placeholder == "<ENT_1>"
    assert len(ctx.placeholder_map) == 1


def test_heuristic_placeholder_never_collides_with_an_unused_registry_token():
    """A registry token reserved for an unmentioned company must not get
    reused by the heuristic fallback for a different company, or reverse_map corrupts."""
    registry = [
        PlaceholderRegistryEntry(surface_form="3M Company", placeholder="<ENT_1>", canonical=CanonicalIdentifiers()),
    ]
    ctx = _ctx()
    # "3M Company" is never mentioned here, but its reserved token must
    # still not be reused for "Acme Corp".
    remapped = remap_query("Acme Corp reported revenue.", ctx, registry=registry)

    assert ctx.placeholder_map == {"<ENT_2>": "Acme Corp"}
    assert remapped == "<ENT_2> reported revenue."


def test_reverse_map_restores_multiple_entities_exactly():
    ctx = _ctx()
    query = "Compare Acme Corp and Beta Inc and Gamma Ltd."
    remapped = remap_query(query, ctx)

    assert reverse_map(remapped, ctx) == query


def test_shorthand_alias_resolves_to_the_canonical_db_matching_name():
    """Metadata store is keyed by the full registrant name, not a shorthand alias.
    placeholder_map must hold the canonical alias or the Postgres exact-match lookup returns nothing."""
    registry = [
        PlaceholderRegistryEntry(surface_form="3M", placeholder="<ENT_1>", canonical=CanonicalIdentifiers(ticker="MMM")),
        PlaceholderRegistryEntry(surface_form="3M Company", placeholder="<ENT_1>", canonical=CanonicalIdentifiers(ticker="MMM")),
    ]
    ctx = _ctx()
    remapped = remap_query("What was 3M's net income?", ctx, registry=registry)

    assert remapped == "What was <ENT_1>'s net income?"
    assert ctx.placeholder_map == {"<ENT_1>": "3M Company"}  # canonical, not the "3M" the query used


def test_reverse_map_is_a_noop_on_text_with_no_placeholders():
    ctx = _ctx()
    ctx.placeholder_map["<ENT_1>"] = "Acme Corp"

    assert reverse_map("plain answer text, no tokens here", ctx) == "plain answer text, no tokens here"
