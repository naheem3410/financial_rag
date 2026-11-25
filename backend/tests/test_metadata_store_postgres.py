"""PostgresMetadataStore tests. Runs against the real project-scoped
postgres:16.4 container.
"""
from __future__ import annotations

from contracts.models import Confidence, NumericRecord, Period, Proposition, PropositionMetadata
from offline.indexing.metadata_store_postgres import PostgresMetadataStore


def _make_proposition(proposition_id: str, company: str = "Acme Corp", fiscal_year=2024, quarter=3, metrics=()) -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=f"{company} operating margin for Q{quarter} FY{fiscal_year} was 18.2 percent.",
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        numeric=NumericRecord(
            value=18.2, scale="unit", currency=None, unit="percent",
            period=Period(fiscal_year=fiscal_year, quarter=quarter),
        ),
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company=company, filing_type="10-Q", filing_date="2024-11-05",
            section="MD&A", page=34, source_uri=f"{company.lower().replace(' ', '_')}_10q.pdf",
        ),
        metrics=tuple(metrics),
    )


def test_upsert_and_query_round_trip_metrics(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", metrics=("net_income", "total_revenue")))

    fetched = store.get("p_1")

    assert fetched.metrics == ("net_income", "total_revenue")


def test_query_filters_by_metric(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_ni", metrics=("net_income",)))
    store.upsert(_make_proposition("p_rev", metrics=("total_revenue",)))

    results = store.query(metrics=["net_income"])

    assert [p.proposition_id for p in results] == ["p_ni"]


def test_query_metrics_matches_any_of_several_requested(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_ni", metrics=("net_income",)))
    store.upsert(_make_proposition("p_rev", metrics=("total_revenue",)))
    store.upsert(_make_proposition("p_margin", metrics=("operating_margin",)))

    results = store.query(metrics=["net_income", "total_revenue"])

    assert {p.proposition_id for p in results} == {"p_ni", "p_rev"}


def test_query_by_metric_matches_a_fact_with_more_than_one_label(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_both", metrics=("net_income", "total_revenue")))

    results = store.query(metrics=["total_revenue"])

    assert [p.proposition_id for p in results] == ["p_both"]


def test_query_without_metrics_ignores_the_filter_entirely(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_labelled", metrics=("net_income",)))
    store.upsert(_make_proposition("p_unlabelled", metrics=()))

    results = store.query()

    assert {p.proposition_id for p in results} == {"p_labelled", "p_unlabelled"}


def test_query_with_an_empty_metrics_list_is_the_same_as_no_filter(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_labelled", metrics=("net_income",)))
    store.upsert(_make_proposition("p_unlabelled", metrics=()))

    results = store.query(metrics=[])

    assert {p.proposition_id for p in results} == {"p_labelled", "p_unlabelled"}


def test_unlabelled_proposition_defaults_to_an_empty_metrics_tuple(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1"))

    assert store.get("p_1").metrics == ()


def test_upsert_strips_null_bytes_rather_than_raising(clean_postgres):
    """Regression (2026-07-25): a pre-fix proposition can still carry a
    stray U+0000, which JSONB hard-rejects -- upsert must sanitize it."""
    store = PostgresMetadataStore(clean_postgres)
    prop = _make_proposition("p_null_byte")
    prop = prop.model_copy(update={"text": "Demand for Post-it\x00ae-solutions grew in 2022."})

    store.upsert(prop)  # must not raise
    fetched = store.get("p_null_byte")

    assert "\x00" not in fetched.text
    assert fetched.text == "Demand for Post-itae-solutions grew in 2022."
    store.close()


def test_upsert_and_get_roundtrip(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    prop = _make_proposition("p_1")

    store.upsert(prop)
    fetched = store.get("p_1")

    assert fetched == prop
    store.close()


def test_ids_reflects_all_upserted_propositions(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1"))
    store.upsert(_make_proposition("p_2"))

    assert store.ids() == {"p_1", "p_2"}
    store.close()


def test_upsert_is_idempotent_on_conflict(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", fiscal_year=2023))
    store.upsert(_make_proposition("p_1", fiscal_year=2024))  # same id, new data

    assert store.ids() == {"p_1"}
    assert store.get("p_1").numeric.period.fiscal_year == 2024
    store.close()


def test_get_missing_id_raises_key_error(clean_postgres):
    import pytest

    store = PostgresMetadataStore(clean_postgres)
    with pytest.raises(KeyError):
        store.get("does_not_exist")
    store.close()


def test_query_filters_by_company(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", company="Acme Corp"))
    store.upsert(_make_proposition("p_2", company="Beta Inc"))

    results = store.query(company="Acme Corp")

    assert {p.proposition_id for p in results} == {"p_1"}
    store.close()


def test_query_filters_by_fiscal_year_and_filing_type(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", fiscal_year=2023))
    store.upsert(_make_proposition("p_2", fiscal_year=2024))

    results = store.query(fiscal_year=2024, filing_type="10-Q")

    assert {p.proposition_id for p in results} == {"p_2"}
    store.close()


class TestDeterministicRowOrder:
    """Regression (2026-07-25): query/all had no ORDER BY, so row order was
    planner-dependent and flipped fusion tie-breaks between process runs."""

    def test_query_returns_rows_in_a_fixed_order_regardless_of_insertion_order(self, clean_postgres):
        store = PostgresMetadataStore(clean_postgres)
        # Insert in reverse-of-expected order -- if ORDER BY were absent,
        # insertion/physical order could plausibly leak through instead.
        for pid in ("p_3", "p_1", "p_2"):
            store.upsert(_make_proposition(pid))

        first = [p.proposition_id for p in store.query()]
        second = [p.proposition_id for p in store.query()]

        assert first == second == ["p_1", "p_2", "p_3"]
        store.close()

    def test_all_returns_rows_in_a_fixed_order(self, clean_postgres):
        store = PostgresMetadataStore(clean_postgres)
        for pid in ("p_z", "p_a", "p_m"):
            store.upsert(_make_proposition(pid))

        assert [p.proposition_id for p in store.all()] == ["p_a", "p_m", "p_z"]
        store.close()


def test_clear_empties_the_table(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1"))

    store.clear()

    assert store.ids() == set()
    store.close()
