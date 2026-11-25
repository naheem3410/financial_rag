"""offline/extraction/backfill_numeric.py against the real test database.
Decision 39: real balance-sheet facts ("at <date>"/"as of <date>") were
indexed before that phrasing was added to extract_tier2, leaving
`numeric=None` for every one of them."""
from __future__ import annotations

from contracts.models import Confidence, NumericRecord, Period, Proposition, PropositionMetadata
from offline.extraction.backfill_numeric import backfill_numeric
from offline.indexing.metadata_store_postgres import PostgresMetadataStore


def _make_proposition(proposition_id: str, text: str, numeric=None) -> Proposition:
    return Proposition(
        proposition_id=proposition_id, text=text, provenance_tier="retrieved", ingestion_path="A_unstructured",
        numeric=numeric, confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="Acme Corp", filing_type="10-K", filing_date="2024-11-05", section="", page=1, source_uri="acme.pdf"),
    )


def test_backfill_recovers_a_balance_sheet_fact_extraction_never_ran_on(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "Acme Corp had accounts payable of 3,183 million at December 31, 2022."))

    report = backfill_numeric(store)

    assert report.recovered == 1
    numeric = store.get("p_1").numeric
    assert numeric is not None
    assert numeric.value == 3183.0
    assert numeric.period.fiscal_year == 2022


def test_backfill_never_touches_a_fact_that_already_has_a_number(clean_postgres):
    existing = NumericRecord(value=1.0, scale="millions", unit="absolute", period=Period(fiscal_year=1999))
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "Acme Corp had accounts payable of 3,183 million at December 31, 2022.", numeric=existing))

    report = backfill_numeric(store)

    assert report.recovered == 0
    assert report.already_had_numeric == 1
    assert store.get("p_1").numeric.value == 1.0  # untouched, not re-decided


def test_backfill_leaves_genuinely_non_numeric_text_alone(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "Acme Corp relocated its headquarters."))

    report = backfill_numeric(store)

    assert report.recovered == 0
    assert store.get("p_1").numeric is None


def test_backfill_run_twice_changes_nothing_the_second_time(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "Acme Corp had accounts payable of 3,183 million at December 31, 2022."))

    first = backfill_numeric(store)
    second = backfill_numeric(store)

    assert first.recovered == 1
    assert second.recovered == 0
    assert store.get("p_1").numeric.value == 3183.0


def test_backfill_does_not_change_the_fact_count(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "Acme Corp had accounts payable of 3,183 million at December 31, 2022."))
    store.upsert(_make_proposition("p_2", "Acme Corp relocated its headquarters."))

    before = len(store.ids())
    backfill_numeric(store)
    after = len(store.ids())

    assert before == after == 2


def test_backfill_report_totals_add_up(clean_postgres):
    existing = NumericRecord(value=1.0, scale="millions", unit="absolute", period=Period(fiscal_year=1999))
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "Acme Corp had accounts payable of 3,183 million at December 31, 2022."))
    store.upsert(_make_proposition("p_2", "Acme Corp relocated its headquarters."))
    store.upsert(_make_proposition("p_3", "text", numeric=existing))

    report = backfill_numeric(store)

    assert report.total == 3
    assert report.already_had_numeric == 1
    assert report.recovered == 1
    assert report.still_unresolved == 1
