"""Keeps the database, the dense index and the keyword index holding the
same facts. The database is the source of truth: a fact missing from one
index is healed from it in the background, never a reason to stop
answering questions. Only a huge mismatch (a wrong file loaded) refuses to
answer. Steps 0.4 (locks) and 0.5 (events) do the plumbing.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import psycopg

from contracts.interfaces import Embedder
from infra.connections import connect_postgres
from infra.events import Event, EventBus, LocalEventBus, new_event
from infra.locks import held_lock
from offline.indexing.build import IndexBundle

SEVERE_MISSING_SHARE = 0.5
REPAIR_LOCK_NAME = "index-repair"
REPAIR_LOCK_TTL_SECONDS = 300


@dataclass(frozen=True)
class IndexDiff:
    dense_missing: tuple[str, ...] = ()  # in the database, not in the dense index
    sparse_missing: tuple[str, ...] = ()  # in the database, not in the keyword index
    dense_ghosts: tuple[str, ...] = ()  # in the dense index, no longer in the database
    sparse_ghosts: tuple[str, ...] = ()  # in the keyword index, no longer in the database

    def is_clean(self) -> bool:
        return not (self.dense_missing or self.sparse_missing or self.dense_ghosts or self.sparse_ghosts)

    def as_event_data(self) -> dict:
        return {
            "dense_missing": list(self.dense_missing), "sparse_missing": list(self.sparse_missing),
            "dense_ghosts": list(self.dense_ghosts), "sparse_ghosts": list(self.sparse_ghosts),
        }


def diff_index_state(db_ids: set[str], dense_ids: set[str], sparse_ids: set[str]) -> IndexDiff:
    return IndexDiff(
        dense_missing=tuple(sorted(db_ids - dense_ids)),
        sparse_missing=tuple(sorted(db_ids - sparse_ids)),
        dense_ghosts=tuple(sorted(dense_ids - db_ids)),
        sparse_ghosts=tuple(sorted(sparse_ids - db_ids)),
    )


def is_severely_broken(diff: IndexDiff, db_count: int) -> bool:
    """A handful of gaps is normal drift; this is "the wrong file got
    loaded" territory instead."""
    if db_count == 0:
        return False
    worst_missing = max(len(diff.dense_missing), len(diff.sparse_missing))
    return worst_missing / db_count > SEVERE_MISSING_SHARE


def run_startup_check(bundle: IndexBundle) -> IndexDiff:
    return diff_index_state(bundle.metadata_store.ids(), bundle.dense_index.ids(), bundle.sparse_index.ids())


def publish_check_result(bus: EventBus, job_id: str, diff: IndexDiff, db_count: int) -> Event:
    """The one place `startup.check_requested` is answered. Always fires
    exactly one event, so a listener never has to guess which happened."""
    if is_severely_broken(diff, db_count):
        worst = max(len(diff.dense_missing), len(diff.sparse_missing))
        event = new_event(
            "index.severely_broken", job_id=job_id,
            message=f"{worst} of {db_count} facts missing from an index; refusing to search until repaired",
            data=diff.as_event_data(),
        )
    elif not diff.is_clean():
        event = new_event(
            "index.mismatch_found", job_id=job_id,
            message="the index drifted from the database; repairing in the background",
            data=diff.as_event_data(),
        )
    else:
        event = new_event("index.check_done", job_id=job_id, message="the index matches the database")
    bus.publish(event)
    return event


def repair_from_event(
    event: Event,
    bundle: IndexBundle,
    embedder: Embedder,
    lock_conn: psycopg.Connection,
    dense_index_path: Union[str, Path],
    sparse_index_path: Union[str, Path],
) -> None:
    """The listener for `index.mismatch_found`. A ghost in the keyword
    index is removed outright. A ghost in the dense (FAISS) index is left
    alone: a flat FAISS index has no in-place delete, so removing it needs
    a full rebuild -- tracked as its own job, not something a live patch
    can do safely. It cannot be served as evidence either way, since
    `metadata_store.get` on a ghost id always raises."""
    dense_missing = event.data.get("dense_missing", [])
    sparse_missing = event.data.get("sparse_missing", [])
    sparse_ghosts = event.data.get("sparse_ghosts", [])
    if not (dense_missing or sparse_missing or sparse_ghosts):
        return

    with held_lock(lock_conn, REPAIR_LOCK_NAME, holder=event.id, ttl_seconds=REPAIR_LOCK_TTL_SECONDS):
        for proposition_id in dense_missing:
            proposition = bundle.metadata_store.get(proposition_id)
            [vector] = embedder.encode_passages([proposition.text])
            bundle.dense_index.upsert(proposition_id, vector)
        for proposition_id in sparse_missing:
            proposition = bundle.metadata_store.get(proposition_id)
            bundle.sparse_index.upsert(proposition_id, proposition.text)
        for proposition_id in sparse_ghosts:
            bundle.sparse_index.remove(proposition_id)

        if dense_missing:
            bundle.dense_index.save(dense_index_path)
        if sparse_missing or sparse_ghosts:
            bundle.sparse_index.save(sparse_index_path)


def run_check_and_repair_once(
    bundle: IndexBundle,
    embedder: Embedder,
    dense_index_path: Union[str, Path],
    sparse_index_path: Union[str, Path],
    bus: EventBus,
    lock_conn: psycopg.Connection,
    job_id: str = "startup",
) -> Event:
    """One full cycle: check, then repair if the check found a normal (not
    severe) mismatch. Returns the check's event either way, so a caller can
    tell what happened without re-deriving it."""
    diff = run_startup_check(bundle)
    event = publish_check_result(bus, job_id, diff, db_count=len(bundle.metadata_store.ids()))
    if event.type == "index.mismatch_found":
        repair_from_event(event, bundle, embedder, lock_conn, dense_index_path, sparse_index_path)
    return event


def start_background_index_check(
    bundle: IndexBundle,
    embedder: Embedder,
    dense_index_path: Union[str, Path],
    sparse_index_path: Union[str, Path],
    postgres_dsn: str,
) -> threading.Thread:
    """Fires the check in a background thread so the app finishes starting
    immediately -- the whole point of step 1.3 is that nothing at startup
    waits on this. Opens its own connection; never touches the one the API
    request path uses."""

    def _run() -> None:
        conn = connect_postgres(postgres_dsn, autocommit=True)
        try:
            bus = LocalEventBus(conn)
            run_check_and_repair_once(bundle, embedder, dense_index_path, sparse_index_path, bus, conn)
        finally:
            conn.close()

    thread = threading.Thread(target=_run, name="index-check", daemon=True)
    thread.start()
    return thread
