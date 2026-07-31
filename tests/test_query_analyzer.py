"""Query analyzer tests."""
from __future__ import annotations

from contracts.models import CanonicalIdentifiers, CollisionTag, PlaceholderRegistryEntry, RequestContext
from online.analysis.query_analyzer import (
    QueryPeriod,
    analyze_query,
    extract_metrics,
    extract_periods,
    record_collision_tags,
)


def test_extract_metrics_matches_known_synonyms():
    metrics = extract_metrics("How did operating margin and net margin compare?")

    assert metrics == ("operating_margin", "net_profit_margin")


def test_extract_metrics_longest_phrase_wins_over_substring():
    metrics = extract_metrics("What was net profit margin in Q3?")

    assert metrics == ("net_profit_margin",)  # not also "net_revenue" or a partial match


def test_extract_periods_single_quarter_fiscal_year():
    periods = extract_periods("What was operating margin in Q3 FY2024?")

    assert periods == (QueryPeriod(fiscal_year=2024, quarter=3),)


def test_extract_periods_two_digit_year_normalizes_to_four_digit():
    periods = extract_periods("Compare Q3 FY23 to Q4 FY24.")

    assert periods == (QueryPeriod(fiscal_year=2023, quarter=3), QueryPeriod(fiscal_year=2024, quarter=4))


def test_extract_periods_fiscal_year_only_not_double_counted_with_quarter():
    periods = extract_periods("Q3 FY2024 vs FY2024 overall")

    assert periods == (QueryPeriod(fiscal_year=2024, quarter=3),)


def test_extract_entities_matches_registry_surface_forms():
    registry = [
        PlaceholderRegistryEntry(surface_form="Acme Corp", placeholder="<ENT_1>", canonical=CanonicalIdentifiers()),
        PlaceholderRegistryEntry(surface_form="Beta Inc", placeholder="<ENT_2>", canonical=CanonicalIdentifiers()),
    ]

    analysis = analyze_query("How did Acme Corp's operating margin compare to Beta Inc's?", registry_entries=registry)

    assert set(analysis.entities) == {"<ENT_1>", "<ENT_2>"}


def test_metric_axis_collision_tagged():
    analysis = analyze_query("Compare operating margin and net margin for Q3 FY2024.")

    assert analysis.collisions == (
        CollisionTag(axis="metric", members=("net_profit_margin", "operating_margin")),
    )


def test_no_metric_collision_when_only_one_metric_present():
    analysis = analyze_query("What was operating margin for Q3 FY2024?")

    assert analysis.collisions == ()


def test_temporal_axis_collision_tagged():
    analysis = analyze_query("Compare operating margin for Q3 FY23 vs Q4 FY24.")

    assert analysis.collisions == (CollisionTag(axis="temporal", members=("Q3 FY2023", "Q4 FY2024")),)


def test_no_temporal_collision_with_a_single_period():
    analysis = analyze_query("What was operating margin for Q3 FY2024?")

    assert not any(c.axis == "temporal" for c in analysis.collisions)


def test_entity_axis_collision_tagged_from_precomputed_pairs_not_recomputed():
    registry = [
        PlaceholderRegistryEntry(surface_form="Acme Corp", placeholder="<ENT_1>", canonical=CanonicalIdentifiers()),
        PlaceholderRegistryEntry(surface_form="Acme Capital", placeholder="<ENT_2>", canonical=CanonicalIdentifiers()),
    ]
    precomputed_pairs = [("<ENT_1>", "<ENT_2>")]

    analysis = analyze_query(
        "How do Acme Corp and Acme Capital compare?",
        registry_entries=registry,
        entity_collision_pairs=precomputed_pairs,
    )

    assert analysis.collisions == (CollisionTag(axis="entity", members=("<ENT_1>", "<ENT_2>")),)


def test_entity_collision_pair_not_flagged_if_only_one_entity_mentioned():
    registry = [
        PlaceholderRegistryEntry(surface_form="Acme Corp", placeholder="<ENT_1>", canonical=CanonicalIdentifiers()),
        PlaceholderRegistryEntry(surface_form="Acme Capital", placeholder="<ENT_2>", canonical=CanonicalIdentifiers()),
    ]
    precomputed_pairs = [("<ENT_1>", "<ENT_2>")]

    analysis = analyze_query(
        "What was Acme Corp's operating margin?",
        registry_entries=registry,
        entity_collision_pairs=precomputed_pairs,
    )

    assert analysis.collisions == ()


def test_collisions_can_span_multiple_axes_at_once():
    registry = [
        PlaceholderRegistryEntry(surface_form="Acme Corp", placeholder="<ENT_1>", canonical=CanonicalIdentifiers()),
        PlaceholderRegistryEntry(surface_form="Acme Capital", placeholder="<ENT_2>", canonical=CanonicalIdentifiers()),
    ]
    analysis = analyze_query(
        "Compare Acme Corp and Acme Capital operating margin and net margin for Q3 FY23 vs Q4 FY24.",
        registry_entries=registry,
        entity_collision_pairs=[("<ENT_1>", "<ENT_2>")],
    )

    axes = {c.axis for c in analysis.collisions}
    assert axes == {"metric", "temporal", "entity"}


def test_unparseable_query_returns_empty_slots_no_llm_call(llm_call_counter):
    """No LLM fallback path exists (module docstring) — an unparseable query
    just returns empty slots."""
    analysis = analyze_query("asdf qwer zxcv")

    assert analysis.entities == ()
    assert analysis.metrics == ()
    assert analysis.periods == ()
    assert analysis.collisions == ()
    assert llm_call_counter.count == 0


def test_record_collision_tags_appends_into_request_context():
    ctx = RequestContext(request_id="r_1", config_hash="cfg_abc")
    analysis = analyze_query("Compare operating margin and net margin for Q3 FY2024.")

    record_collision_tags(analysis, ctx)

    assert ctx.collision_tags == [CollisionTag(axis="metric", members=("net_profit_margin", "operating_margin"))]


def test_record_collision_tags_accumulates_across_multiple_sub_queries():
    """A full query typically analyzes several sub-queries; tags from each
    must accumulate in the shared per-request context, not overwrite."""
    ctx = RequestContext(request_id="r_1", config_hash="cfg_abc")
    first = analyze_query("Compare operating margin and net margin for Q3 FY2024.")
    second = analyze_query("Compare Q3 FY23 to Q4 FY24.")

    record_collision_tags(first, ctx)
    record_collision_tags(second, ctx)

    assert len(ctx.collision_tags) == 2
    assert {c.axis for c in ctx.collision_tags} == {"metric", "temporal"}
