"""offline/documents/policy_ingestor.py: the real, end-to-end path from an
uploaded file's bytes to searchable PolicyFact rows -- and through the real
upload job (step 3.3), not just the ingestor in isolation."""
from __future__ import annotations

import psycopg
import pytest

from infra.events import LocalEventBus
from offline.documents.document_card import get_document
from offline.documents.file_store import InMemoryFileStore
from offline.documents.policy_answer import assemble_policy_answer, render_policy_answer
from offline.documents.policy_fact_store import PolicyFactStore
from offline.documents.policy_ingestor import check_policy_facts_stored, ingest_policy_document, remove_policy_facts
from offline.documents.upload_job import run_ingest_step, submit_upload

_POLICY_TEXT = (
    "4.2.1 Account Eligibility for Minors\n"
    "Applicants under the age of 18 years are permitted to open a custodial "
    "savings account. However, a parent or legal guardian must be designated "
    "as a joint account holder. This does not apply to emancipated minors.\n"
    "4.2.2 Account Eligibility for Seniors\n"
    "Applicants aged 65 or older receive a waiver on the monthly maintenance fee.\n"
)


@pytest.fixture(autouse=True)
def _clean(clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    for table in ("documents", "policy_facts", "bus_events", "bus_cursors", "bus_failures", "shared_locks", "idempotency_keys"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.close()


@pytest.fixture
def conn(clean_postgres):
    connection = psycopg.connect(clean_postgres, autocommit=True)
    yield connection
    connection.close()


@pytest.fixture
def fact_store(clean_postgres):
    store = PolicyFactStore(clean_postgres)
    yield store
    store.close()


@pytest.fixture
def bus(conn):
    return LocalEventBus(conn)


@pytest.fixture
def file_store():
    return InMemoryFileStore()


def _card_kwargs(title="Account Handbook", file_name="handbook.txt"):
    return {"title": title, "doc_type": "policy", "file_name": file_name}


def test_a_real_policy_document_is_chunked_and_indexed_through_the_real_upload_job(conn, bus, file_store, fact_store):
    ingest_fn = lambda card, file_bytes: ingest_policy_document(card, file_bytes, fact_store)
    check_fn = lambda fact_ids: check_policy_facts_stored(fact_store, fact_ids)

    submit_upload(conn, file_store, bus, "t-1", "doc-1", _POLICY_TEXT.encode("utf-8"), _card_kwargs(), ingest_fn)
    run_ingest_step(conn, file_store, bus, "doc-1", ingest_fn, check_stores_fn=check_fn)

    card = get_document(conn, "doc-1")
    assert card.indexed is True
    assert len(card.fact_ids) == 2

    facts = fact_store.query("doc-1")
    minors_fact = next(f for f in facts if f.section_number == "4.2.1")
    assert minors_fact.section_path == ("Account Eligibility for Minors",)
    assert minors_fact.consequent == "Applicants under the age of 18 years are permitted to open a custodial savings account."
    assert minors_fact.exceptions == (
        "a parent or legal guardian must be designated as a joint account holder.",
        "emancipated minors.",
    )


def test_the_rendered_answer_matches_the_agreed_format_end_to_end(conn, bus, file_store, fact_store):
    ingest_fn = lambda card, file_bytes: ingest_policy_document(card, file_bytes, fact_store)
    submit_upload(conn, file_store, bus, "t-1", "doc-1", _POLICY_TEXT.encode("utf-8"), _card_kwargs(), ingest_fn)
    run_ingest_step(conn, file_store, bus, "doc-1", ingest_fn)
    minors_fact = next(f for f in fact_store.query("doc-1") if f.section_number == "4.2.1")

    answer = assemble_policy_answer(minors_fact, document_title="Account Handbook")
    rendered = render_policy_answer(answer)

    assert rendered.startswith(
        "Applicants under the age of 18 years are permitted to open a custodial savings account.\n\n"
    )
    assert "-- Account Handbook, page 1, Section 4.2.1 (Account Eligibility for Minors)" in rendered


def test_replacing_a_policy_document_removes_the_old_facts_and_writes_new_ones(conn, bus, file_store, fact_store):
    ingest_fn = lambda card, file_bytes: ingest_policy_document(card, file_bytes, fact_store)
    remove_fn = lambda fact_ids: remove_policy_facts(fact_store, fact_ids)

    submit_upload(conn, file_store, bus, "t-1", "doc-1", _POLICY_TEXT.encode("utf-8"), _card_kwargs(), ingest_fn)
    run_ingest_step(conn, file_store, bus, "doc-1", ingest_fn, remove_fn=remove_fn)
    old_fact_ids = set(fact_store.ids())
    assert len(old_fact_ids) == 2

    new_text = "4.2.1 Account Eligibility for Minors\nAll minors are now barred from opening accounts.\n"
    submit_upload(conn, file_store, bus, "t-2", "doc-2", new_text.encode("utf-8"), _card_kwargs(file_name="handbook-v2.txt"), ingest_fn)
    run_ingest_step(conn, file_store, bus, "doc-2", ingest_fn, remove_fn=remove_fn)

    remaining_ids = fact_store.ids()
    assert remaining_ids.isdisjoint(old_fact_ids)  # every old fact really gone
    assert len(remaining_ids) == 1
    assert fact_store.query("doc-2")[0].consequent == "All minors are now barred from opening accounts."


def test_deleting_a_policy_document_removes_its_facts_too(conn, bus, file_store, fact_store):
    from offline.documents.upload_job import delete_document

    ingest_fn = lambda card, file_bytes: ingest_policy_document(card, file_bytes, fact_store)
    remove_fn = lambda fact_ids: remove_policy_facts(fact_store, fact_ids)
    submit_upload(conn, file_store, bus, "t-1", "doc-1", _POLICY_TEXT.encode("utf-8"), _card_kwargs(), ingest_fn)
    run_ingest_step(conn, file_store, bus, "doc-1", ingest_fn, remove_fn=remove_fn)
    assert len(fact_store.ids()) == 2

    delete_document(conn, file_store, bus, "doc-1", remove_fn=remove_fn)

    assert fact_store.ids() == set()


def test_check_policy_facts_stored_catches_a_genuinely_missing_fact(fact_store):
    assert check_policy_facts_stored(fact_store, ["never_written"]) is False


def test_antecedent_extraction_flows_through_the_real_ingest_pipeline_end_to_end(conn, bus, file_store, fact_store):
    class _FakeAntecedentClient:
        def complete(self, payload):
            if "18" in payload["rule_block"]:
                return {"reasoning": "...", "antecedent": "Applicants under the age of 18 years"}
            return {"reasoning": "...", "antecedent": None}

    ingest_fn = lambda card, file_bytes: ingest_policy_document(
        card, file_bytes, fact_store, antecedent_llm_client=_FakeAntecedentClient(),
    )
    submit_upload(conn, file_store, bus, "t-1", "doc-1", _POLICY_TEXT.encode("utf-8"), _card_kwargs(), ingest_fn)
    run_ingest_step(conn, file_store, bus, "doc-1", ingest_fn)

    minors_fact = next(f for f in fact_store.query("doc-1") if f.section_number == "4.2.1")
    assert minors_fact.antecedent == "Applicants under the age of 18 years"


def test_without_an_antecedent_client_the_ingest_pipeline_behaves_exactly_as_before(conn, bus, file_store, fact_store):
    ingest_fn = lambda card, file_bytes: ingest_policy_document(card, file_bytes, fact_store)  # no antecedent_llm_client
    submit_upload(conn, file_store, bus, "t-1", "doc-1", _POLICY_TEXT.encode("utf-8"), _card_kwargs(), ingest_fn)
    run_ingest_step(conn, file_store, bus, "doc-1", ingest_fn)

    minors_fact = next(f for f in fact_store.query("doc-1") if f.section_number == "4.2.1")
    assert minors_fact.antecedent is None


def test_check_policy_facts_stored_passes_when_everything_landed(conn, bus, file_store, fact_store):
    ingest_fn = lambda card, file_bytes: ingest_policy_document(card, file_bytes, fact_store)
    submit_upload(conn, file_store, bus, "t-1", "doc-1", _POLICY_TEXT.encode("utf-8"), _card_kwargs(), ingest_fn)
    run_ingest_step(conn, file_store, bus, "doc-1", ingest_fn)

    assert check_policy_facts_stored(fact_store, get_document(conn, "doc-1").fact_ids) is True
