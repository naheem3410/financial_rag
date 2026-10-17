"""Ties the document card, the file store, the lock, the idempotency
ticket and the event bus into one real "upload a document" job. Step 3.3,
decision 15.

Turning a file's bytes into searchable facts is pluggable (`ingest_fn`) --
what a "fact" means for a policy, a form, or a filing is a separate
question this module deliberately stays out of. Its job is only to do
that work exactly once, safely, with a repeated upload recognized, a new
version replacing the old one's facts, and every step visible as an event.

Two halves, on purpose:
- `submit_upload` is the fast path: dedupe, save the card and the file,
  publish `document.uploaded`, return. Nothing slow happens on this path.
- `run_ingest_step` is the slow path: take the lock, turn the bytes into
  facts, check the stores agree, record the result. `submit_upload` fires
  this in a background thread (`start_background_ingest`) so the caller
  never waits on it -- the same one-shot "fire it in the background, open
  its own connection" pattern step 1.3 uses for the startup index check. A
  full production worker instead subscribes to `document.uploaded` once
  and dispatches continuously, in its own process; this proves the one
  piece of that a single call can prove, not the whole always-on service.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import psycopg

from infra.connections import connect_postgres
from infra.events import EventBus, LocalEventBus, new_event
from infra.idempotency import TicketOutcome, run_once
from infra.locks import LockBusy, acquire_lock, release_lock
from offline.documents.document_card import (
    DocumentCard,
    create_document,
    delete_document_row,
    find_completed_by_fingerprint,
    find_previous_completed_version,
    get_document,
    mark_indexed,
    mark_superseded,
)
from offline.documents.file_store import FileStore, fingerprint as compute_fingerprint

UPLOAD_LOCK_TTL_SECONDS = 600
DEFAULT_MAX_INGEST_ATTEMPTS = 3
# "Two uploads at once -> one waits for the lock": infra.locks.acquire_lock
# itself fails fast (right, for callers that want to know at once); the
# upload job is the one that actually needs to queue behind another upload
# of the same document, so it retries here instead.
LOCK_WAIT_SECONDS = 30
LOCK_RETRY_DELAY_SECONDS = 0.05


@dataclass(frozen=True)
class IngestResult:
    """What the pluggable content step reports back: the fact ids it wrote,
    so the job can record them, check the stores agree, and remove them
    cleanly on a later replace or delete."""
    fact_ids: tuple[str, ...]


IngestFn = Callable[[DocumentCard, bytes], IngestResult]
RemoveFn = Callable[[Sequence[str]], None]
CheckStoresFn = Callable[[Sequence[str]], bool]


def _lock_name(card: DocumentCard) -> str:
    return f"document:{card.title}:{card.doc_type}"


def _acquire_lock_with_wait(conn: psycopg.Connection, name: str, holder: str) -> None:
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            acquire_lock(conn, name, holder, UPLOAD_LOCK_TTL_SECONDS)
            return
        except LockBusy:
            if time.monotonic() >= deadline:
                raise
            time.sleep(LOCK_RETRY_DELAY_SECONDS)


def submit_upload(
    conn: psycopg.Connection,
    file_store: FileStore,
    bus: EventBus,
    ticket: str,
    doc_id: str,
    file_bytes: bytes,
    card_kwargs: dict,
    ingest_fn: IngestFn,
    remove_fn: Optional[RemoveFn] = None,
    check_stores_fn: Optional[CheckStoresFn] = None,
    max_ingest_attempts: int = DEFAULT_MAX_INGEST_ATTEMPTS,
    postgres_dsn: Optional[str] = None,
) -> TicketOutcome:
    """Returns as soon as the file and card are saved -- never waits on
    turning the file into facts. `doc_id` is the caller's choice (stable
    across a retry of the same ticket) so a killed-and-retried job resumes
    the same document row instead of making a second one.

    `postgres_dsn` given: the ingest step is fired in a real background
    thread on its own connection (the production shape). Left out (the
    default in tests that want a deterministic result to assert on): the
    caller runs `run_ingest_step` itself, synchronously, whenever it wants."""

    def _do_submit() -> dict:
        digest = compute_fingerprint(file_bytes)
        duplicate = find_completed_by_fingerprint(conn, digest, exclude_id=doc_id)
        if duplicate is not None:
            bus.publish(new_event(
                "document.skipped", job_id=doc_id,
                message="already there", data={"existing_doc_id": duplicate.id},
            ))
            return {"status": "skipped_already_there", "doc_id": duplicate.id}

        card = create_document(conn, doc_id=doc_id, fingerprint=digest, **card_kwargs)
        file_store.save(card.id, file_bytes)
        bus.publish(new_event("document.uploaded", job_id=card.id, message="saved; working on it in the background", data={"doc_id": card.id}))

        if postgres_dsn is not None:
            start_background_ingest(
                postgres_dsn, file_store, card.id, ingest_fn, remove_fn, check_stores_fn, max_ingest_attempts,
            )
        return {"status": "accepted", "doc_id": card.id}

    return run_once(conn, ticket, card_kwargs, _do_submit)


def run_ingest_step(
    conn: psycopg.Connection,
    file_store: FileStore,
    bus: EventBus,
    doc_id: str,
    ingest_fn: IngestFn,
    remove_fn: Optional[RemoveFn] = None,
    check_stores_fn: Optional[CheckStoresFn] = None,
    max_ingest_attempts: int = DEFAULT_MAX_INGEST_ATTEMPTS,
) -> None:
    """The slow half: take the lock, turn the file into facts, check the
    stores agree, record the result. Safe to call more than once for the
    same document -- an already-indexed one is left alone, so a duplicate
    `document.uploaded` delivery (or a worker retrying after a crash) never
    redoes finished work."""
    card = get_document(conn, doc_id)
    if card.indexed:
        return
    file_bytes = file_store.read(doc_id)

    lock_name = _lock_name(card)
    _acquire_lock_with_wait(conn, lock_name, card.id)
    try:
        card = get_document(conn, doc_id)  # re-read: another worker may have finished it while this one waited
        if card.indexed:
            return

        previous = find_previous_completed_version(conn, card.title, card.doc_type, exclude_id=card.id)
        if previous is not None:
            if remove_fn is not None and previous.fact_ids:
                remove_fn(previous.fact_ids)
            mark_superseded(conn, previous.id)

        result = _run_ingest_with_retries(bus, card, file_bytes, ingest_fn, max_ingest_attempts)

        if check_stores_fn is not None and not check_stores_fn(result.fact_ids):
            bus.publish(new_event("document.failed", job_id=card.id, message="the stores did not agree after ingest"))
            raise RuntimeError(f"three-store check failed for {card.id}")

        mark_indexed(conn, card.id, result.fact_ids)
        bus.publish(new_event("document.indexed", job_id=card.id, message="done", data={"fact_ids": list(result.fact_ids)}))
    finally:
        release_lock(conn, lock_name, card.id)


def start_background_ingest(
    postgres_dsn: str,
    file_store: FileStore,
    doc_id: str,
    ingest_fn: IngestFn,
    remove_fn: Optional[RemoveFn] = None,
    check_stores_fn: Optional[CheckStoresFn] = None,
    max_ingest_attempts: int = DEFAULT_MAX_INGEST_ATTEMPTS,
) -> threading.Thread:
    """Runs `run_ingest_step` for one document on a background thread with
    its own connection and event bus -- the caller (and its own connection)
    is free the instant this returns, which is at once."""

    def _run() -> None:
        conn = connect_postgres(postgres_dsn, autocommit=True)
        try:
            bus = LocalEventBus(conn)
            run_ingest_step(conn, file_store, bus, doc_id, ingest_fn, remove_fn, check_stores_fn, max_ingest_attempts)
        except Exception:
            pass  # already reported as a document.failed event; a background thread must never crash the process
        finally:
            conn.close()

    thread = threading.Thread(target=_run, name=f"ingest-{doc_id}", daemon=True)
    thread.start()
    return thread


def _run_ingest_with_retries(
    bus: EventBus, card: DocumentCard, file_bytes: bytes, ingest_fn: IngestFn, max_attempts: int
) -> IngestResult:
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return ingest_fn(card, file_bytes)
        except Exception as exc:  # noqa: BLE001 -- any failure in the pluggable step is retried, then reported plainly
            last_error = exc
            if attempt < max_attempts:
                bus.publish(new_event(
                    "document.retry", job_id=card.id,
                    message=f"attempt {attempt} of {max_attempts} failed: {exc}",
                ))
    bus.publish(new_event("document.failed", job_id=card.id, message=str(last_error)))
    raise RuntimeError(f"ingest failed for {card.id} after {max_attempts} attempts") from last_error


def delete_document(
    conn: psycopg.Connection,
    file_store: FileStore,
    bus: EventBus,
    doc_id: str,
    remove_fn: Optional[RemoveFn] = None,
) -> None:
    """Removes a document's facts from the other stores, its file, and its
    card -- in that order, so a failure partway never leaves a fact
    findable with no file or card behind it."""
    card = get_document(conn, doc_id)
    if remove_fn is not None and card.fact_ids:
        remove_fn(card.fact_ids)
    file_store.delete(doc_id)
    delete_document_row(conn, doc_id)
    bus.publish(new_event("document.deleted", job_id=doc_id, message="removed", data={"fact_ids": list(card.fact_ids)}))
