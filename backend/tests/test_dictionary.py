"""infra/dictionary.py against the real MongoDB and Redis boxes. Step 2.2."""
from __future__ import annotations

import pytest

from infra.connections import connect_mongo, connect_redis
from infra.dictionary import (
    ensure_dictionary_indexes,
    known_phrases,
    load_dictionary_into_redis,
    log_unmatched_phrase,
    lookup_metric,
    seed_default_dictionary,
)

MONGO_URI = "mongodb://localhost:27017"
REDIS_URL = "redis://localhost:6379/15"  # db 15: a scratch db, never the real one


@pytest.fixture
def mongo_collection():
    client = connect_mongo(MONGO_URI)
    db = client["financial_rag_test"]
    collection = db["metric_dictionary"]
    collection.delete_many({})
    ensure_dictionary_indexes(collection)
    yield collection
    collection.delete_many({})
    client.close()


@pytest.fixture
def unmatched_collection():
    client = connect_mongo(MONGO_URI)
    db = client["financial_rag_test"]
    collection = db["unmatched_phrases"]
    collection.delete_many({})
    yield collection
    collection.delete_many({})
    client.close()


@pytest.fixture
def redis_client():
    client = connect_redis(REDIS_URL)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


def test_seed_writes_the_metric_name_and_its_aliases(mongo_collection):
    aliases = {"net_income": ("net income",)}

    written = seed_default_dictionary(mongo_collection, aliases)

    phrases = {doc["phrase"] for doc in mongo_collection.find({})}
    # the canonical metric name ("net income", underscores replaced) and its
    # one alias are literally the same phrase here, so exactly one document
    assert phrases == {"net income"}
    assert written == 1


def test_seed_distinguishes_a_metric_name_from_its_alias_when_they_differ(mongo_collection):
    aliases = {"total_revenue": ("net sales", "total net sales")}

    seed_default_dictionary(mongo_collection, aliases)

    phrases = {doc["phrase"] for doc in mongo_collection.find({})}
    assert phrases == {"total revenue", "net sales", "total net sales"}


def test_seed_is_safe_to_run_twice(mongo_collection):
    aliases = {"net_income": ("net income",)}

    seed_default_dictionary(mongo_collection, aliases)
    second_written = seed_default_dictionary(mongo_collection, aliases)

    assert second_written == 0
    assert mongo_collection.count_documents({}) == 1


def test_seed_never_overwrites_a_phrase_from_a_different_source(mongo_collection):
    """A phrase already curated by hand (or from another source) must not
    be silently replaced by a later seed run."""
    mongo_collection.insert_one({"phrase": "net income", "metric": "net_income", "source": "hand-curated", "version": 3})

    seed_default_dictionary(mongo_collection, {"net_income": ("net income",)})

    doc = mongo_collection.find_one({"phrase": "net income"})
    assert doc["source"] == "hand-curated"
    assert doc["version"] == 3


def test_load_into_redis_then_lookup_finds_a_seeded_phrase(mongo_collection, redis_client):
    seed_default_dictionary(mongo_collection, {"net_income": ("net income",)})

    load_dictionary_into_redis(mongo_collection, redis_client)

    assert lookup_metric(redis_client, "net income") == "net_income"


def test_lookup_is_case_insensitive(mongo_collection, redis_client):
    seed_default_dictionary(mongo_collection, {"net_income": ("net income",)})
    load_dictionary_into_redis(mongo_collection, redis_client)

    assert lookup_metric(redis_client, "NET INCOME") == "net_income"
    assert lookup_metric(redis_client, "Net Income") == "net_income"


def test_lookup_of_an_unknown_phrase_returns_none(redis_client):
    assert lookup_metric(redis_client, "something nobody taught it") is None


def test_load_returns_how_many_phrases_it_loaded(mongo_collection, redis_client):
    seed_default_dictionary(mongo_collection, {"net_income": ("net income",), "total_revenue": ("net sales",)})

    count = load_dictionary_into_redis(mongo_collection, redis_client)

    assert count == 3  # "net income", "total revenue", "net sales"


def test_known_phrases_reflects_what_was_loaded(mongo_collection, redis_client):
    seed_default_dictionary(mongo_collection, {"net_income": ("net income",)})
    load_dictionary_into_redis(mongo_collection, redis_client)

    assert known_phrases(redis_client) == {"net income"}


def test_a_dictionary_change_is_picked_up_by_reloading(mongo_collection, redis_client):
    """MongoDB is the source; Redis is only ever a copy of it."""
    seed_default_dictionary(mongo_collection, {"net_income": ("net income",)})
    load_dictionary_into_redis(mongo_collection, redis_client)
    assert lookup_metric(redis_client, "operating margin") is None

    mongo_collection.insert_one({"phrase": "operating margin", "metric": "operating_margin", "source": "manual", "version": 1})
    load_dictionary_into_redis(mongo_collection, redis_client)

    assert lookup_metric(redis_client, "operating margin") == "operating_margin"


def test_log_unmatched_phrase_is_durable_and_counts_repeats(unmatched_collection):
    log_unmatched_phrase(unmatched_collection, "some odd phrase")
    log_unmatched_phrase(unmatched_collection, "some odd phrase")
    log_unmatched_phrase(unmatched_collection, "SOME ODD PHRASE")  # case folded to the same entry

    doc = unmatched_collection.find_one({"phrase": "some odd phrase"})
    assert doc["count"] == 3


def test_unique_index_prevents_two_different_metrics_for_the_same_phrase(mongo_collection):
    mongo_collection.insert_one({"phrase": "net income", "metric": "net_income", "source": "a", "version": 1})

    with pytest.raises(Exception):  # pymongo.errors.DuplicateKeyError, kept generic to avoid importing pymongo here
        mongo_collection.insert_one({"phrase": "net income", "metric": "something_else", "source": "b", "version": 1})
