"""online/routing/retry_with_other_names.py: try again with other names
(step 4.5). Runs against the real project-scoped postgres:16.4 container,
same as test_metadata_filter.py -- this is exact-match SQL filtering, real
infrastructure, not a fake worth mocking.
"""
from __future__ import annotations

from contracts.models import (
    CanonicalIdentifiers, Confidence, NumericRecord, PlaceholderRegistryEntry, Period,
    Proposition, PropositionMetadata, RequestContext, SubQuery,
)
from offline.indexing.metadata_store_postgres import PostgresMetadataStore
from online.routing.retry_with_other_names import filter_with_retry, other_surface_forms

REGISTRY = [
    PlaceholderRegistryEntry(surface_form="Acme", placeholder="<ENT_1>", canonical=CanonicalIdentifiers(ticker="ACME")),
    PlaceholderRegistryEntry(surface_form="Acme Corporation", placeholder="<ENT_1>", canonical=CanonicalIdentifiers(ticker="ACME")),
    PlaceholderRegistryEntry(surface_form="Beta", placeholder="<ENT_2>", canonical=CanonicalIdentifiers(ticker="BETA")),
]


def _seeded_store(dsn: str) -> PostgresMetadataStore:
    store = PostgresMetadataStore(dsn)
    prop = Proposition(
        proposition_id="p_1",
        text="Acme Corporation operating margin for FY2024 was 18.2 percent.",
        provenance_tier="retrieved", ingestion_path="A_unstructured",
        numeric=NumericRecord(value=18.2, scale="unit", currency=None, unit="percent", period=Period(fiscal_year=2024)),
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="Acme Corporation", filing_type="10-K", filing_date="2024-11-05",
            section="MD&A", page=1, source_uri="acme.pdf",
        ),
    )
    store.upsert(prop)
    return store


# --- other_surface_forms ---


def test_other_surface_forms_returns_the_sibling_sharing_the_placeholder():
    result = other_surface_forms("<ENT_1>", REGISTRY, already_tried="Acme")

    assert result == ["Acme Corporation"]


def test_other_surface_forms_excludes_the_one_already_tried():
    result = other_surface_forms("<ENT_1>", REGISTRY, already_tried="Acme Corporation")

    assert result == ["Acme"]


def test_other_surface_forms_ignores_a_different_companys_names():
    result = other_surface_forms("<ENT_2>", REGISTRY, already_tried="Beta")

    assert result == []


def test_other_surface_forms_empty_for_an_unregistered_placeholder():
    result = other_surface_forms("<ENT_99>", REGISTRY, already_tried="Whatever Inc")

    assert result == []


# --- filter_with_retry ---


def test_a_non_empty_first_try_never_retries(clean_postgres):
    store = _seeded_store(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme Corporation"})
    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_1>", metric="", period=Period(fiscal_year=2024), period_label="FY2024")

    outcome = filter_with_retry(sub_query, ctx, store, REGISTRY)

    assert outcome.retried is False
    assert outcome.names_tried == ()
    assert outcome.found is True
    assert [p.proposition_id for p in outcome.candidates] == ["p_1"]
    store.close()


def test_an_empty_first_try_retries_with_the_other_known_name_and_finds_it(clean_postgres):
    """The plan's own "weak result, other name works" case: the resolved
    name ("Acme", the short registry form) doesn't match what's actually
    stored ("Acme Corporation") -- the retry, using the sibling name from
    the same registry entry, finds it and says so."""
    store = _seeded_store(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme"})
    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_1>", metric="", period=Period(fiscal_year=2024), period_label="FY2024")

    outcome = filter_with_retry(sub_query, ctx, store, REGISTRY)

    assert outcome.retried is True
    assert outcome.names_tried == ("Acme Corporation",)
    assert outcome.found is True
    assert [p.proposition_id for p in outcome.candidates] == ["p_1"]
    store.close()


def test_a_second_weak_result_is_an_honest_not_found_no_third_try(clean_postgres):
    """Neither the resolved name nor its only known sibling matches
    anything -- one retry, then an honest empty result, never a third try."""
    store = PostgresMetadataStore(clean_postgres)  # nothing seeded at all
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme"})
    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_1>", metric="", period=Period(fiscal_year=2024), period_label="FY2024")

    outcome = filter_with_retry(sub_query, ctx, store, REGISTRY)

    assert outcome.retried is True
    assert outcome.names_tried == ("Acme Corporation",)
    assert outcome.found is False
    assert outcome.candidates == ()
    store.close()


def test_no_known_alternate_name_means_no_retry_attempted(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_2>": "Beta"})
    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_2>", metric="", period=Period(fiscal_year=2024), period_label="FY2024")

    outcome = filter_with_retry(sub_query, ctx, store, REGISTRY)

    assert outcome.retried is False
    assert outcome.found is False
    store.close()


def test_passthrough_entity_with_no_resolved_company_never_retries(clean_postgres):
    """NPCE-off: entity is raw query text, not a placeholder -- nothing to
    look up a sibling name for."""
    store = PostgresMetadataStore(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg")
    sub_query = SubQuery(sub_query_id="sq_1", entity="a plain question", metric="", period=Period(fiscal_year=2024), period_label="FY2024")

    outcome = filter_with_retry(sub_query, ctx, store, REGISTRY)

    assert outcome.retried is False
    assert outcome.found is False
    store.close()
