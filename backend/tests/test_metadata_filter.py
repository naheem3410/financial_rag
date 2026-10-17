"""Metadata filter tests. Runs against the real project-scoped
postgres:16.4 container.
"""
from __future__ import annotations

from contracts.models import Confidence, Period, Proposition, PropositionMetadata, RequestContext, SubQuery
from offline.indexing.metadata_store_postgres import PostgresMetadataStore
from online.retrieval.metadata_filter import filter_candidates


def _seeded_store(dsn: str) -> PostgresMetadataStore:
    from contracts.models import NumericRecord

    store = PostgresMetadataStore(dsn)
    for pid, company, fy, q in [
        ("p_1", "Acme Corp", 2024, 3),
        ("p_2", "Acme Corp", 2023, 3),
        ("p_3", "Beta Inc", 2024, 3),
    ]:
        prop = Proposition(
            proposition_id=pid,
            text=f"{company} operating margin for Q{q} FY{fy} was 18.2 percent.",
            provenance_tier="retrieved",
            ingestion_path="A_unstructured",
            numeric=NumericRecord(value=18.2, scale="unit", currency=None, unit="percent", period=Period(fiscal_year=fy, quarter=q)),
            confidence=Confidence(extraction_tier="rules"),
            metadata=PropositionMetadata(
                company=company, filing_type="10-Q", filing_date="2024-11-05",
                section="MD&A", page=34, source_uri=f"{company.lower().replace(' ', '_')}.pdf",
            ),
        )
        store.upsert(prop)
    return store


def _seeded_store_with_metrics(dsn: str) -> PostgresMetadataStore:
    from contracts.models import NumericRecord

    store = PostgresMetadataStore(dsn)
    facts = [
        ("p_ni", "net income of $5,777 million", ("net_income",)),
        ("p_rev", "total net sales of $34,229 million", ("total_revenue",)),
        ("p_other", "some unrelated statement about headquarters", ()),
    ]
    for pid, phrase, metrics in facts:
        prop = Proposition(
            proposition_id=pid,
            text=f"Acme Corp had {phrase} for FY2024.",
            provenance_tier="retrieved", ingestion_path="A_unstructured",
            numeric=NumericRecord(value=1.0, scale="unit", currency="USD", unit="absolute", period=Period(fiscal_year=2024)),
            confidence=Confidence(extraction_tier="rules"),
            metadata=PropositionMetadata(company="Acme Corp", filing_type="10-K", filing_date="2024-11-05", section="MD&A", page=1, source_uri="acme.pdf"),
            metrics=metrics,
        )
        store.upsert(prop)
    return store


def test_metric_filter_off_by_default_returns_everything_in_scope(clean_postgres):
    store = _seeded_store_with_metrics(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme Corp"})
    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_1>", metric="net_income", period=Period(fiscal_year=2024), period_label="FY2024")

    results = filter_candidates(sub_query, ctx, store)  # enable_metric_filter defaults False

    assert {p.proposition_id for p in results} == {"p_ni", "p_rev", "p_other"}
    store.close()


def test_metric_filter_on_narrows_to_the_labelled_facts(clean_postgres):
    store = _seeded_store_with_metrics(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme Corp"})
    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_1>", metric="net_income", period=Period(fiscal_year=2024), period_label="FY2024")

    results = filter_candidates(sub_query, ctx, store, enable_metric_filter=True)

    assert {p.proposition_id for p in results} == {"p_ni"}
    store.close()


def test_metric_filter_falls_back_to_company_and_year_when_it_would_leave_nothing(clean_postgres):
    """The metric label is missing or wrong for every candidate -- rather
    than a silent empty answer, fall back to the wider company+year set."""
    store = _seeded_store_with_metrics(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme Corp"})
    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_1>", metric="gross_margin", period=Period(fiscal_year=2024), period_label="FY2024")

    results = filter_candidates(sub_query, ctx, store, enable_metric_filter=True)

    assert {p.proposition_id for p in results} == {"p_ni", "p_rev", "p_other"}
    store.close()


def test_metric_filter_with_no_metric_on_the_sub_query_applies_no_metric_constraint(clean_postgres):
    store = _seeded_store_with_metrics(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme Corp"})
    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_1>", metric="", period=Period(fiscal_year=2024), period_label="FY2024")

    results = filter_candidates(sub_query, ctx, store, enable_metric_filter=True)

    assert {p.proposition_id for p in results} == {"p_ni", "p_rev", "p_other"}
    store.close()


def test_resolves_placeholder_to_real_company_and_filters(clean_postgres):
    store = _seeded_store(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme Corp"})
    sub_query = SubQuery(
        sub_query_id="sq_1", entity="<ENT_1>", metric="operating_margin",
        period=Period(fiscal_year=2024, quarter=3), period_label="Q3 FY2024",
    )

    results = filter_candidates(sub_query, ctx, store)

    assert {p.proposition_id for p in results} == {"p_1"}
    store.close()


def test_filters_out_wrong_fiscal_year(clean_postgres):
    store = _seeded_store(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme Corp"})
    sub_query = SubQuery(
        sub_query_id="sq_1", entity="<ENT_1>", metric="operating_margin",
        period=Period(fiscal_year=2023, quarter=3), period_label="Q3 FY2023",
    )

    results = filter_candidates(sub_query, ctx, store)

    assert {p.proposition_id for p in results} == {"p_2"}
    store.close()


def test_unresolvable_placeholder_fails_closed_returns_nothing(clean_postgres):
    """Unmapped placeholder must not silently drop the company constraint --
    that would let a wrong-issuer proposition through."""
    store = _seeded_store(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={})  # no mapping
    sub_query = SubQuery(
        sub_query_id="sq_1", entity="<ENT_99>", metric="operating_margin",
        period=Period(fiscal_year=2024, quarter=3), period_label="Q3 FY2024",
    )

    results = filter_candidates(sub_query, ctx, store)

    assert results == []
    store.close()


def test_passthrough_style_entity_applies_no_company_filter(clean_postgres):
    """NPCE-off: entity holds raw query text, not a placeholder, so it
    must not be treated as a company name to filter on."""
    store = _seeded_store(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg")
    sub_query = SubQuery(
        sub_query_id="sq_1", entity="What was operating margin in Q3 FY2024?", metric="",
        period=Period(fiscal_year=2024, quarter=3), period_label="Q3 FY2024",
    )

    results = filter_candidates(sub_query, ctx, store)

    assert {p.proposition_id for p in results} == {"p_1", "p_3"}  # both companies, matching period
    store.close()


def test_unconstrained_period_sentinel_applies_no_period_filter(clean_postgres):
    store = _seeded_store(clean_postgres)
    ctx = RequestContext(request_id="r_1", config_hash="cfg", placeholder_map={"<ENT_1>": "Acme Corp"})
    sub_query = SubQuery(
        sub_query_id="sq_1", entity="<ENT_1>", metric="operating_margin",
        period=Period(fiscal_year=0), period_label="",
    )

    results = filter_candidates(sub_query, ctx, store)

    assert {p.proposition_id for p in results} == {"p_1", "p_2"}  # both Acme periods
    store.close()
