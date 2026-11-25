"""Placeholder remapper tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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


def test_concurrent_remap_entity_on_the_same_context_never_corrupts_the_map():
    """Decision 38's defensive fix: `_placeholder_for`'s read-then-write on
    `ctx.placeholder_map` is now guarded by a lock (online/placeholders/
    remapper.py). Today's real pipeline call sites never call
    `remap_entity` from more than one thread on the same `ctx` (all
    remapping finishes before pipeline_c4.py's parallel loops start, see
    its own comment on that) -- this test guards the function itself,
    so that stays true even if a future caller calls it concurrently.

    Many threads resolve a mix of a shared registry entity, several
    distinct heuristic-fallback entities, and the SAME heuristic entity
    repeated many times, all on one shared `ctx`. Without the lock, two
    threads racing to mint a fresh `<ENT_n>` token for the same new
    surface form could both see the token free and both write, leaving
    one placeholder silently pointing at the wrong company, or the same
    surface form resolving to two different tokens."""
    ctx = _ctx()
    registry = [
        PlaceholderRegistryEntry(surface_form="3M Company", placeholder="<ENT_1>", canonical=CanonicalIdentifiers(ticker="MMM")),
    ]
    # 16 repeats of the registry entity, 16 repeats of one shared new
    # surface, and 20 genuinely distinct new surfaces -- a real mix of
    # "same token every time" and "mint it exactly once" pressure.
    surfaces = (
        ["3M Company"] * 16
        + ["Acme Corp"] * 16
        + [f"Beta{i} Inc" for i in range(20)]
    )

    results: dict[str, set[str]] = {}

    def _resolve(surface: str) -> tuple[str, str]:
        return surface, remap_entity(surface, ctx, registry=registry)

    with ThreadPoolExecutor(max_workers=16) as executor:
        for surface, placeholder in executor.map(_resolve, surfaces):
            results.setdefault(surface, set()).add(placeholder)

    # Every surface resolved to exactly one placeholder across every call,
    # from every thread -- never two different tokens for the same company
    # from a lost update racing the mint.
    assert all(len(placeholders) == 1 for placeholders in results.values()), results
    assert results["3M Company"] == {"<ENT_1>"}

    # No two distinct surfaces ended up sharing one placeholder either.
    placeholder_by_surface = {surface: next(iter(p)) for surface, p in results.items()}
    assert len(set(placeholder_by_surface.values())) == len(placeholder_by_surface)

    # The map itself is internally consistent: every placeholder this run
    # minted reverse-maps back to exactly the surface that earned it.
    for surface, placeholder in placeholder_by_surface.items():
        assert ctx.placeholder_map[placeholder] == surface
        assert reverse_map(placeholder, ctx) == surface
