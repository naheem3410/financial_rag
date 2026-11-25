"""offline/labeling/backfill_metrics.py against the real test database. Step 1.4."""
from __future__ import annotations

from contracts.models import Confidence, Proposition, PropositionMetadata
from offline.indexing.metadata_store_postgres import PostgresMetadataStore
from offline.labeling.backfill_metrics import backfill_metrics, sample_for_review


def _make_proposition(proposition_id: str, text: str) -> Proposition:
    return Proposition(
        proposition_id=proposition_id, text=text, provenance_tier="retrieved", ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="Acme Corp", filing_type="10-Q", filing_date="2024-11-05", section="MD&A", page=1, source_uri="acme.pdf"),
    )


def test_backfill_labels_facts_with_a_recognizable_metric(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "Acme Corp had net income of $5 million."))

    report = backfill_metrics(store)

    assert report.total == 1
    assert report.labelled == 1
    assert report.changed == 1
    assert store.get("p_1").metrics == ("net_income",)


def test_backfill_leaves_unrecognizable_facts_unlabelled(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "The company relocated its headquarters."))

    report = backfill_metrics(store)

    assert report.labelled == 0
    assert store.get("p_1").metrics == ()


def test_backfill_run_twice_gives_the_same_result_and_changes_nothing_the_second_time(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "Acme Corp had net income of $5 million."))
    store.upsert(_make_proposition("p_2", "Total net sales were $10 million."))

    first = backfill_metrics(store)
    second = backfill_metrics(store)

    assert second.changed == 0
    assert second.labelled == first.labelled
    assert store.get("p_1").metrics == ("net_income",)
    assert store.get("p_2").metrics == ("total_revenue",)


def test_backfill_does_not_change_the_fact_count(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    for i in range(5):
        store.upsert(_make_proposition(f"p_{i}", "Acme Corp had net income of $5 million."))

    before = len(store.ids())
    backfill_metrics(store)
    after = len(store.ids())

    assert before == after == 5


def test_backfill_reports_the_labelled_share(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "Acme Corp had net income of $5 million."))
    store.upsert(_make_proposition("p_2", "The company relocated its headquarters."))

    report = backfill_metrics(store)

    assert report.total == 2
    assert report.labelled == 1
    assert report.labelled_share == 0.5


def test_backfill_relabels_if_a_facts_text_changes(clean_postgres):
    """Not "safe to run twice" in the sense of never touching a row again --
    it always reflects the current text, so a correction to a fact's text
    is picked up on the next run."""
    store = PostgresMetadataStore(clean_postgres)
    prop = _make_proposition("p_1", "The company relocated its headquarters.")
    store.upsert(prop)
    backfill_metrics(store)
    assert store.get("p_1").metrics == ()

    corrected = prop.model_copy(update={"text": "Net income was $5 million."})
    store.upsert(corrected)
    report = backfill_metrics(store)

    assert report.changed == 1
    assert store.get("p_1").metrics == ("net_income",)


def test_sample_for_review_returns_at_most_n_propositions(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    for i in range(5):
        store.upsert(_make_proposition(f"p_{i}", "text"))

    sample = sample_for_review(store, n=3, seed=1)

    assert len(sample) == 3


def test_sample_for_review_never_exceeds_the_total_count(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "text"))

    sample = sample_for_review(store, n=100, seed=1)

    assert len(sample) == 1


# --- model fallback: only reached for facts the rules found nothing on ---


class _SequencedClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def test_backfill_without_a_client_never_touches_the_model(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "The company relocated its headquarters."))

    report = backfill_metrics(store)  # llm_client=None, the default

    assert report.labelled_by_model == 0
    assert store.get("p_1").metrics == ()


def test_backfill_with_a_client_only_calls_it_for_facts_rules_missed(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_ni", "Acme Corp had net income of $5 million."))  # rules get this one
    store.upsert(_make_proposition("p_ambiguous", "The board approved a new capital plan."))  # rules miss this one
    client = _SequencedClient([{"reasoning": "...", "metrics": ["capital_expenditure"]}])

    report = backfill_metrics(store, llm_client=client)

    assert len(client.calls) == 1  # only the one rules missed
    assert report.labelled_by_rules == 1
    assert report.labelled_by_model == 1
    assert store.get("p_ni").metrics == ("net_income",)
    assert store.get("p_ambiguous").metrics == ("capital_expenditure",)


def test_backfill_with_a_client_still_leaves_a_fact_unlabelled_if_the_model_also_finds_nothing(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    store.upsert(_make_proposition("p_1", "The company relocated its headquarters."))
    client = _SequencedClient([{"reasoning": "...", "metrics": []}])

    report = backfill_metrics(store, llm_client=client)

    assert report.labelled_by_model == 0
    assert store.get("p_1").metrics == ()


def test_sample_for_review_is_reproducible_with_the_same_seed(clean_postgres):
    store = PostgresMetadataStore(clean_postgres)
    for i in range(10):
        store.upsert(_make_proposition(f"p_{i}", "text"))

    first = sample_for_review(store, n=3, seed=42)
    second = sample_for_review(store, n=3, seed=42)

    assert [p.proposition_id for p in first] == [p.proposition_id for p in second]
