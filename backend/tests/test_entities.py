"""offline/registry/entities.py: the one shared company list. Step 2.1."""
from __future__ import annotations

import psycopg
import pytest

from contracts.models import Confidence, NumericRecord, Period, Proposition, PropositionMetadata
from offline.indexing.metadata_store_postgres import PostgresMetadataStore
from offline.registry.entities import Entity, build_registry, list_entities, update_entity_ticker
from infra.versioning import VersionConflict


def _make_proposition(proposition_id: str, company: str, ticker=None) -> Proposition:
    return Proposition(
        proposition_id=proposition_id, text=f"{company} had net income of $1 million.",
        provenance_tier="retrieved", ingestion_path="A_unstructured",
        numeric=NumericRecord(value=1.0, scale="unit", currency="USD", unit="absolute", period=Period(fiscal_year=2024)),
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company=company, ticker=ticker, filing_type="10-K", filing_date="2024-01-01", section="", page=1, source_uri=f"{company}.pdf"),
    )


@pytest.fixture
def conn(clean_postgres):
    connection = psycopg.connect(clean_postgres, autocommit=True)
    connection.execute("DELETE FROM entities")
    yield connection
    connection.execute("DELETE FROM entities")
    connection.close()


# --- ingestion keeps the list current, no code change needed ---


def test_a_new_company_shows_up_from_ingestion_alone(clean_postgres, conn):
    store = PostgresMetadataStore(clean_postgres)

    store.upsert(_make_proposition("p_1", "3M Company", ticker="MMM"))

    entities = list_entities(conn)
    assert entities == [Entity(company="3M Company", ticker="MMM", version=1)]
    store.close()


def test_a_second_fact_for_the_same_company_does_not_duplicate_the_entity(clean_postgres, conn):
    store = PostgresMetadataStore(clean_postgres)

    store.upsert(_make_proposition("p_1", "3M Company", ticker="MMM"))
    store.upsert(_make_proposition("p_2", "3M Company", ticker="MMM"))

    assert len(list_entities(conn)) == 1
    store.close()


def test_a_later_fact_with_no_ticker_does_not_erase_an_already_known_one(clean_postgres, conn):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "3M Company", ticker="MMM"))

    store.upsert(_make_proposition("p_2", "3M Company", ticker=None))

    assert list_entities(conn)[0].ticker == "MMM"
    store.close()


def test_legacy_facts_written_before_step_2_1_self_heal_into_the_entities_table(clean_postgres, conn):
    """Facts already in the database, from before this table existed, must
    still show up once a store connects -- no manual backfill script."""
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "3M Company", ticker="MMM"))
    store.close()
    conn.execute("DELETE FROM entities")  # simulate: facts exist, but no entity row yet

    healed_store = PostgresMetadataStore(clean_postgres)  # __init__ self-heals

    entities = list_entities(conn)
    assert entities == [Entity(company="3M Company", ticker="MMM", version=1)]
    healed_store.close()


def test_self_heal_prefers_a_ticker_over_a_missing_one_across_companies_facts(clean_postgres, conn):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "3M Company", ticker=None))
    store.upsert(_make_proposition("p_2", "3M Company", ticker="MMM"))
    store.close()
    conn.execute("DELETE FROM entities")

    healed_store = PostgresMetadataStore(clean_postgres)

    assert list_entities(conn)[0].ticker == "MMM"
    healed_store.close()


def test_self_heal_does_not_run_again_once_the_table_has_rows(clean_postgres, conn):
    """Only the empty-table case triggers the backfill scan -- an ordinary
    reconnect must not redo it or disturb an existing entity."""
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "3M Company", ticker="MMM"))
    update_entity_ticker(conn, "3M Company", "EDITED", expected_version=1)

    reconnected = PostgresMetadataStore(clean_postgres)

    assert list_entities(conn)[0].ticker == "EDITED"  # not reverted by a fresh scan
    store.close()
    reconnected.close()


def test_store_entities_method_matches_list_entities(clean_postgres, conn):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "3M Company", ticker="MMM"))

    assert store.entities() == list_entities(conn)
    store.close()


# --- build_registry: the rules already learned, moved here unchanged ---


def test_all_indexed_companies_resolve_to_the_full_name():
    entities = [Entity(company="3M Company", ticker="MMM", version=1), Entity(company="Adobe Inc.", ticker="ADBE", version=1)]

    registry = build_registry(entities)

    by_form = {e.surface_form: e.placeholder for e in registry}
    assert by_form["3M Company"] == by_form["3M"]
    assert by_form["Adobe Inc."] == by_form["Adobe"]


def test_possessive_of_the_short_name_resolves():
    entities = [Entity(company="3M Company", ticker="MMM", version=1)]

    registry = build_registry(entities)

    surface_forms = {e.surface_form for e in registry}
    assert "3M's" in surface_forms


def test_possessive_of_the_full_name_is_excluded_for_being_longer_than_the_full_name():
    """Regression: the remapper treats the longest alias as the real company
    name, so "3M Company's" (longer than "3M Company") must never appear --
    this was a real bug fixed earlier this session."""
    entities = [Entity(company="3M Company", ticker="MMM", version=1)]

    registry = build_registry(entities)

    surface_forms = {e.surface_form for e in registry}
    assert "3M Company's" not in surface_forms


def test_no_variant_is_ever_longer_than_the_full_company_name():
    entities = [Entity(company="3M Company", ticker="MMM", version=1)]

    registry = build_registry(entities)

    for entry in registry:
        assert len(entry.surface_form) <= len("3M Company")


def test_a_short_name_shared_by_two_companies_is_dropped():
    entities = [
        Entity(company="Delta Air Lines", ticker="DAL", version=1),
        Entity(company="Delta Corp", ticker="DLTA", version=1),
    ]

    registry = build_registry(entities)

    surface_forms = {e.surface_form for e in registry}
    assert "Delta" not in surface_forms
    assert "Delta Air Lines" in surface_forms
    assert "Delta Corp" in surface_forms


def test_empty_entity_list_gives_an_empty_registry():
    assert build_registry([]) == []


# --- version number stops a second, stale edit ---


def test_two_edits_at_once_on_one_entity_the_second_is_stopped(clean_postgres, conn):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "3M Company", ticker="MMM"))
    store.close()

    update_entity_ticker(conn, "3M Company", "NEWTICK", expected_version=1)

    with pytest.raises(VersionConflict):
        update_entity_ticker(conn, "3M Company", "STALE_EDIT", expected_version=1)  # already moved to version 2

    assert list_entities(conn)[0].ticker == "NEWTICK"
