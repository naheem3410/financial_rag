"""PostgresMetadataStore tests. Runs against the real project-scoped
postgres:16.4 container.
"""
from __future__ import annotations

from contracts.models import Confidence, NumericRecord, Period, Proposition, PropositionMetadata
from offline.indexing.metadata_store_postgres import PostgresMetadataStore


def _make_proposition(proposition_id: str, company: str = "Acme Corp", fiscal_year=2024, quarter=3) -> Proposition:
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
    )


def test_upsert_strips_null_bytes_rather_than_raising(clean_postgres):
    """Regression, 2026-07-25: a real 3M/Activision PDF's font decoded a
    dash/trademark glyph to a literal U+0000 in the extracted text
    ("Post-it\\x00ae-solutions"). offline/parsers/layout_parser.py now
    strips this at extraction time, but a proposition built before that fix
    existed (e.g. from an already-checkpointed offline/full_build.py run)
    can still carry the byte -- PostgreSQL's JSONB type hard-rejects U+0000
    outright (`UntranslatableCharacter`), so upsert must sanitize rather
    than propagate that failure."""
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
    """Regression, 2026-07-25: `query`/`all` had no ORDER BY -- row order
    was whatever the query planner happened to produce, no guarantee. This
    matters beyond cosmetics: `online/retrieval/hybrid.py:retrieve` iterates
    `candidates` (typically `query`'s result) to build `ScoredProposition`s,
    and `online/retrieval/fusion.py:reciprocal_rank_fusion`'s *stable* sort
    then uses that input order to break ties on equal dense/sparse scores.
    Caught via a real, reproducible symptom: the same evaluation question
    against the same index flipped between a rerank hit and a miss across
    separate process runs of evaluation/run_retrieval_gate.py."""

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
