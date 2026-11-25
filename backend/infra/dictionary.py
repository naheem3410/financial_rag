"""The metric phrase dictionary: kept in MongoDB (the source), loaded into
Redis at startup (the fast copy everything actually looks phrases up in).
One lookup function, so nothing ever reads MongoDB on the hot path.

Seeded today from the 19-metric lexicon `online/analysis/query_splitter.py`
already used, plus `offline/labeling/metric_labeler.py`'s phrase aliases --
two sources already in the codebase, combined into one place. The fuller
dictionary (accounting taxonomies, glossaries, Nigerian banking terms,
labels pulled from ingested filings, benchmark fields -- each checked for
licence first) is still to be gathered; this module's job is the seam that
work drops into, not the content itself.
"""
from __future__ import annotations

import time
from typing import Mapping, Optional, Sequence

_REDIS_KEY_PREFIX = "dict:metric:"
_REDIS_LOADED_MARKER = "dict:metric:_loaded_at"


def ensure_dictionary_indexes(collection) -> None:
    collection.create_index("phrase", unique=True)


def seed_default_dictionary(
    collection, aliases: Mapping[str, Sequence[str]], source: str = "seed:builtin_aliases"
) -> int:
    """Writes phrase -> metric into MongoDB. A phrase already present from a
    different source is left alone (first writer wins); safe to call twice."""
    written = 0
    for metric, phrases in aliases.items():
        all_phrases = set(phrases) | {metric.replace("_", " ")}
        for phrase in all_phrases:
            result = collection.update_one(
                {"phrase": phrase.lower()},
                {"$setOnInsert": {"phrase": phrase.lower(), "metric": metric, "source": source, "version": 1}},
                upsert=True,
            )
            if result.upserted_id is not None:
                written += 1
    return written


def load_dictionary_into_redis(collection, redis_client) -> int:
    """The one place MongoDB is read for lookups -- called at startup, and
    only again if the dictionary changes. Everything else reads Redis."""
    pipeline = redis_client.pipeline()
    count = 0
    for doc in collection.find({}, {"phrase": 1, "metric": 1}):
        pipeline.set(_REDIS_KEY_PREFIX + doc["phrase"], doc["metric"])
        count += 1
    pipeline.set(_REDIS_LOADED_MARKER, str(int(time.time())))
    pipeline.execute()
    return count


def lookup_metric(redis_client, phrase: str) -> Optional[str]:
    """The one lookup function -- the splitter, the labeler, anything else
    that needs "what metric is this phrase" calls this, never Redis or
    MongoDB directly."""
    value = redis_client.get(_REDIS_KEY_PREFIX + phrase.lower())
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else value


def log_unmatched_phrase(collection, phrase: str) -> None:
    """Durable, in MongoDB, not the Redis cache -- a phrase nobody has
    labelled yet must survive a Redis restart so the dictionary can grow
    from it later (decision 9)."""
    collection.update_one(
        {"phrase": phrase.lower()},
        {"$inc": {"count": 1}, "$set": {"last_seen": time.time()}},
        upsert=True,
    )


def known_phrases(redis_client) -> set[str]:
    """Every phrase currently loaded, with the `dict:metric:` prefix and the
    loaded-at marker stripped off -- mainly for tests and diagnostics."""
    keys = redis_client.keys(_REDIS_KEY_PREFIX + "*")
    phrases = set()
    for key in keys:
        key = key.decode("utf-8") if isinstance(key, bytes) else key
        if key == _REDIS_LOADED_MARKER:
            continue
        phrases.add(key[len(_REDIS_KEY_PREFIX):])
    return phrases
