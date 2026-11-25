"""offline/documents/policy_fact_store.py against the real test database."""
from __future__ import annotations

import psycopg
import pytest

from contracts.models import PolicyFact
from offline.documents.policy_fact_store import PolicyFactStore


@pytest.fixture(autouse=True)
def _clean(clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    conn.execute("DROP TABLE IF EXISTS policy_facts")
    conn.close()


def _make_fact(fact_id="f_1", document_id="doc_1", **overrides) -> PolicyFact:
    defaults = dict(
        fact_id=fact_id, document_id=document_id, text="Minors may open an account.",
        section_path=("Accounts", "Eligibility"), section_number="4.2", consequent="Minors may open an account.",
        page=0, source_uri="policy.pdf",
    )
    defaults.update(overrides)
    return PolicyFact(**defaults)


def test_upsert_and_get_round_trip(clean_postgres):
    store = PolicyFactStore(clean_postgres)
    fact = _make_fact()

    store.upsert(fact)

    assert store.get("f_1") == fact
    store.close()


def test_get_missing_fact_raises_key_error(clean_postgres):
    store = PolicyFactStore(clean_postgres)

    with pytest.raises(KeyError):
        store.get("no-such-fact")
    store.close()


def test_upsert_is_idempotent_on_conflict(clean_postgres):
    store = PolicyFactStore(clean_postgres)
    store.upsert(_make_fact(text="first version"))

    store.upsert(_make_fact(text="second version"))

    assert store.get("f_1").text == "second version"
    assert len(store.ids()) == 1
    store.close()


def test_query_returns_only_facts_for_the_given_document(clean_postgres):
    store = PolicyFactStore(clean_postgres)
    store.upsert(_make_fact(fact_id="f_1", document_id="doc_a"))
    store.upsert(_make_fact(fact_id="f_2", document_id="doc_b"))

    results = store.query("doc_a")

    assert [f.fact_id for f in results] == ["f_1"]
    store.close()


def test_ids_reflects_all_upserted_facts(clean_postgres):
    store = PolicyFactStore(clean_postgres)
    store.upsert(_make_fact(fact_id="f_1"))
    store.upsert(_make_fact(fact_id="f_2"))

    assert store.ids() == {"f_1", "f_2"}
    store.close()


def test_delete_many_removes_the_given_facts_and_leaves_others(clean_postgres):
    store = PolicyFactStore(clean_postgres)
    store.upsert(_make_fact(fact_id="f_1"))
    store.upsert(_make_fact(fact_id="f_2"))

    store.delete_many(["f_1"])

    assert store.ids() == {"f_2"}
    store.close()


def test_delete_many_with_an_empty_list_does_nothing(clean_postgres):
    store = PolicyFactStore(clean_postgres)
    store.upsert(_make_fact(fact_id="f_1"))

    store.delete_many([])

    assert store.ids() == {"f_1"}
    store.close()


def test_a_fact_with_exceptions_round_trips_correctly(clean_postgres):
    store = PolicyFactStore(clean_postgres)
    fact = _make_fact(exceptions=("emancipated minors are exempt.",))

    store.upsert(fact)

    assert store.get("f_1").exceptions == ("emancipated minors are exempt.",)
    store.close()
