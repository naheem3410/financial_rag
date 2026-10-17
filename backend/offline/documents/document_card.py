"""The document card: what a person fills in when they upload a paper, and
what the computer fills in by itself. Step 3.1.

A person gives: title, type, product/area, audience, effective date (or
"not sure"), and an optional version label. The computer fills in: id, file
name, upload date, uploader, kind, and (once the file is processed) a page
count. Two documents with the same title and type are treated as versions
of one another; whichever has the newest effective date is "current".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import psycopg
from psycopg.rows import dict_row

from infra.versioning import optimistic_update

# "Pick lists can differ per organisation" -- this is the default, not the
# only allowed set; callers may pass their own to create_document/DocumentCard.
DEFAULT_DOCUMENT_TYPES: tuple[str, ...] = (
    "policy", "procedure", "product guide", "faq", "form or notice", "other",
)
DEFAULT_AUDIENCE = "everyone"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    product_area TEXT,
    audience TEXT NOT NULL DEFAULT 'everyone',
    effective_date DATE NOT NULL,
    doc_version_label TEXT,
    file_name TEXT NOT NULL,
    uploader TEXT,
    kind TEXT,
    page_count INT,
    upload_date TIMESTAMPTZ NOT NULL DEFAULT now(),
    version INT NOT NULL DEFAULT 1,
    fingerprint TEXT,
    indexed BOOLEAN NOT NULL DEFAULT FALSE,
    fact_ids TEXT[] NOT NULL DEFAULT '{}'
)
"""
# A table created before step 3.3 has none of these three columns yet --
# `CREATE TABLE IF NOT EXISTS` above is a no-op for it, so heal it the same
# way steps 1.4/2.1 do. Safe on every call: all three are no-ops once applied.
_ADD_UPLOAD_COLUMNS_IF_MISSING = """
ALTER TABLE documents ADD COLUMN IF NOT EXISTS fingerprint TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS indexed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS fact_ids TEXT[] NOT NULL DEFAULT '{}';
"""


class DocumentCardError(ValueError):
    """The card is missing something required, or names a type not on the
    pick list -- refused plainly rather than guessed at or half-saved."""


@dataclass(frozen=True)
class DocumentCard:
    id: str
    title: str
    doc_type: str
    file_name: str
    audience: str
    effective_date: date
    upload_date: datetime
    version: int
    product_area: Optional[str] = None
    doc_version_label: Optional[str] = None
    uploader: Optional[str] = None
    kind: Optional[str] = None
    page_count: Optional[int] = None
    fingerprint: Optional[str] = None
    indexed: bool = False
    fact_ids: tuple[str, ...] = ()


def ensure_documents_table(conn: psycopg.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.execute(_ADD_UPLOAD_COLUMNS_IF_MISSING)


def _fetchone_as_dict(conn: psycopg.Connection, query: str, params=()) -> Optional[dict]:
    """Reads a row as a dict regardless of the connection's own row
    factory -- callers of this module (the upload job) also hand the same
    connection to infra.locks/idempotency/events, which expect plain tuple
    rows, so this module never assumes the connection's default."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _fetchall_as_dicts(conn: psycopg.Connection, query: str, params=()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _guess_kind(file_name: str) -> Optional[str]:
    suffix = Path(file_name).suffix.lstrip(".").lower()
    return suffix or None


def create_document(
    conn: psycopg.Connection,
    title: str,
    doc_type: str,
    file_name: str,
    audience: str = DEFAULT_AUDIENCE,
    product_area: Optional[str] = None,
    effective_date: Optional[date] = None,  # None ("not sure") -> upload date
    doc_version_label: Optional[str] = None,
    uploader: Optional[str] = None,
    allowed_types: Sequence[str] = DEFAULT_DOCUMENT_TYPES,
    doc_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> DocumentCard:
    """`doc_id` is normally left out (a fresh id is generated). The upload
    job (step 3.3) passes one in deliberately, derived from its ticket, so
    retrying a killed job re-runs this safely instead of creating a second
    row: on conflict the existing row wins untouched, never overwritten."""
    if not title or not title.strip():
        raise DocumentCardError("a document needs a title")
    if doc_type not in allowed_types:
        raise DocumentCardError(f"{doc_type!r} is not a known document type; choose one of {list(allowed_types)}")
    if not file_name or not file_name.strip():
        raise DocumentCardError("a document needs a file name")

    ensure_documents_table(conn)
    resolved_doc_id = doc_id or uuid.uuid4().hex[:12]
    upload_date = datetime.now(timezone.utc)
    resolved_effective_date = effective_date or upload_date.date()
    kind = _guess_kind(file_name)

    conn.execute(
        """
        INSERT INTO documents
            (id, title, doc_type, product_area, audience, effective_date, doc_version_label,
             file_name, uploader, kind, upload_date, fingerprint)
        VALUES (%(id)s, %(title)s, %(doc_type)s, %(product_area)s, %(audience)s, %(effective_date)s,
                %(doc_version_label)s, %(file_name)s, %(uploader)s, %(kind)s, %(upload_date)s, %(fingerprint)s)
        ON CONFLICT (id) DO NOTHING
        """,
        {
            "id": resolved_doc_id, "title": title, "doc_type": doc_type, "product_area": product_area,
            "audience": audience, "effective_date": resolved_effective_date,
            "doc_version_label": doc_version_label, "file_name": file_name, "uploader": uploader,
            "kind": kind, "upload_date": upload_date, "fingerprint": fingerprint,
        },
    )
    return get_document(conn, resolved_doc_id)


def _row_to_card(row: dict) -> DocumentCard:
    return DocumentCard(
        id=row["id"], title=row["title"], doc_type=row["doc_type"], product_area=row["product_area"],
        audience=row["audience"], effective_date=row["effective_date"], doc_version_label=row["doc_version_label"],
        file_name=row["file_name"], uploader=row["uploader"], kind=row["kind"], page_count=row["page_count"],
        upload_date=row["upload_date"], version=row["version"], fingerprint=row["fingerprint"],
        indexed=row["indexed"], fact_ids=tuple(row["fact_ids"]),
    )


def get_document(conn: psycopg.Connection, doc_id: str) -> DocumentCard:
    ensure_documents_table(conn)
    row = _fetchone_as_dict(conn, "SELECT * FROM documents WHERE id = %s", (doc_id,))
    if row is None:
        raise KeyError(doc_id)
    return _row_to_card(row)


def list_documents(conn: psycopg.Connection) -> list[DocumentCard]:
    ensure_documents_table(conn)
    rows = _fetchall_as_dicts(conn, "SELECT * FROM documents ORDER BY title, doc_type, effective_date DESC")
    return [_row_to_card(r) for r in rows]


def current_documents(conn: psycopg.Connection) -> list[DocumentCard]:
    """One row per (title, doc_type) group: whichever has the newest
    effective date. A tie is broken by the most recently uploaded."""
    ensure_documents_table(conn)
    rows = _fetchall_as_dicts(
        conn,
        """
        SELECT DISTINCT ON (title, doc_type) *
        FROM documents
        ORDER BY title, doc_type, effective_date DESC, upload_date DESC
        """,
    )
    return [_row_to_card(r) for r in rows]


def set_page_count(conn: psycopg.Connection, doc_id: str, page_count: int) -> None:
    """The computer fills this in once the file is actually processed --
    not known at card-creation time."""
    ensure_documents_table(conn)
    conn.execute("UPDATE documents SET page_count = %s WHERE id = %s", (page_count, doc_id))


def mark_indexed(conn: psycopg.Connection, doc_id: str, fact_ids: Sequence[str]) -> None:
    """Records what the ingest step actually wrote, once it succeeds --
    used later to find and remove those same ids on a replace or a delete."""
    ensure_documents_table(conn)
    conn.execute(
        "UPDATE documents SET indexed = TRUE, fact_ids = %s WHERE id = %s",
        (list(fact_ids), doc_id),
    )


def mark_superseded(conn: psycopg.Connection, doc_id: str) -> None:
    """A replaced version's facts are gone from the other stores -- clear
    its indexed flag and fact ids so it can never again be found as "the
    current one to protect" or claim facts it no longer occupies."""
    ensure_documents_table(conn)
    conn.execute("UPDATE documents SET indexed = FALSE, fact_ids = '{}' WHERE id = %s", (doc_id,))


def find_completed_by_fingerprint(conn: psycopg.Connection, fingerprint: str, exclude_id: Optional[str] = None) -> Optional[DocumentCard]:
    """A genuine repeat upload: the same bytes, already fully indexed,
    under a different id. `exclude_id` keeps a job's own retry (same id,
    not yet marked indexed) from matching itself."""
    ensure_documents_table(conn)
    row = _fetchone_as_dict(
        conn,
        "SELECT * FROM documents WHERE fingerprint = %s AND indexed = TRUE AND id != %s "
        "ORDER BY upload_date DESC LIMIT 1",
        (fingerprint, exclude_id or ""),
    )
    return _row_to_card(row) if row else None


def find_previous_completed_version(conn: psycopg.Connection, title: str, doc_type: str, exclude_id: str) -> Optional[DocumentCard]:
    """The most recent already-indexed document sharing this title and
    type, other than the one being uploaded now -- the version a new
    upload replaces."""
    ensure_documents_table(conn)
    row = _fetchone_as_dict(
        conn,
        "SELECT * FROM documents WHERE title = %s AND doc_type = %s AND indexed = TRUE AND id != %s "
        "ORDER BY effective_date DESC, upload_date DESC LIMIT 1",
        (title, doc_type, exclude_id),
    )
    return _row_to_card(row) if row else None


def delete_document_row(conn: psycopg.Connection, doc_id: str) -> None:
    ensure_documents_table(conn)
    conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def update_document_card(
    conn: psycopg.Connection,
    doc_id: str,
    expected_version: int,
    set_sql: str,
    set_params: dict,
) -> None:
    """A rare, deliberate edit (title, type, dates, ...) -- guarded by the
    version number a person read before editing (step 0.4)."""
    ensure_documents_table(conn)
    optimistic_update(conn, "documents", "id", doc_id, expected_version, set_sql, set_params)
