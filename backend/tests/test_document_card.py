"""offline/documents/document_card.py against the real test database. Step 3.1."""
from __future__ import annotations

from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from infra.versioning import VersionConflict
from offline.documents.document_card import (
    DocumentCardError,
    create_document,
    current_documents,
    delete_document_row,
    find_completed_by_fingerprint,
    find_previous_completed_version,
    get_document,
    list_documents,
    mark_indexed,
    set_page_count,
    update_document_card,
)


@pytest.fixture
def conn(clean_postgres):
    connection = psycopg.connect(clean_postgres, row_factory=dict_row, autocommit=True)
    connection.execute("DROP TABLE IF EXISTS documents")
    yield connection
    connection.execute("DROP TABLE IF EXISTS documents")
    connection.close()


def test_a_card_with_only_the_required_fields_is_saved_with_defaults_filled(conn):
    card = create_document(conn, title="Account Opening Policy", doc_type="policy", file_name="policy.pdf")

    assert card.audience == "everyone"
    assert card.effective_date == card.upload_date.date()  # "not sure" -> upload date
    assert card.version == 1
    assert card.kind == "pdf"
    assert card.id  # computer-filled


def test_not_sure_for_the_date_uses_the_upload_date(conn):
    card = create_document(conn, title="Loan Procedure", doc_type="procedure", file_name="loan.pdf", effective_date=None)

    assert card.effective_date == card.upload_date.date()


def test_an_explicit_effective_date_is_kept(conn):
    given = date(2024, 1, 15)

    card = create_document(conn, title="FAQ", doc_type="faq", file_name="faq.pdf", effective_date=given)

    assert card.effective_date == given


def test_missing_title_is_refused_with_a_plain_message(conn):
    with pytest.raises(DocumentCardError, match="title"):
        create_document(conn, title="", doc_type="policy", file_name="p.pdf")

    assert list_documents(conn) == []  # nothing half-saved


def test_missing_title_whitespace_only_is_also_refused(conn):
    with pytest.raises(DocumentCardError, match="title"):
        create_document(conn, title="   ", doc_type="policy", file_name="p.pdf")


def test_a_type_not_on_the_pick_list_is_refused(conn):
    with pytest.raises(DocumentCardError, match="not a known document type"):
        create_document(conn, title="Something", doc_type="manifesto", file_name="p.pdf")

    assert list_documents(conn) == []


def test_a_custom_pick_list_is_honoured(conn):
    """"Pick lists can differ per organisation.\""""
    card = create_document(
        conn, title="Something", doc_type="internal memo", file_name="p.pdf",
        allowed_types=("internal memo", "policy"),
    )

    assert card.doc_type == "internal memo"


def test_missing_file_name_is_refused(conn):
    with pytest.raises(DocumentCardError, match="file name"):
        create_document(conn, title="Something", doc_type="policy", file_name="")


def test_computer_fills_in_id_upload_date_and_kind(conn):
    card = create_document(conn, title="Something", doc_type="policy", file_name="notes.docx")

    assert card.kind == "docx"
    assert card.upload_date is not None
    assert len(card.id) > 0


def test_get_document_round_trips(conn):
    created = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf")

    fetched = get_document(conn, created.id)

    assert fetched == created


def test_get_document_missing_id_raises_key_error(conn):
    with pytest.raises(KeyError):
        get_document(conn, "no-such-id")


def test_set_page_count_is_filled_in_later_once_known(conn):
    card = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf")
    assert card.page_count is None

    set_page_count(conn, card.id, 12)

    assert get_document(conn, card.id).page_count == 12


# --- two versions of a policy: newest effective date is "current" ---


def test_two_versions_of_a_policy_the_newest_is_current(conn):
    create_document(conn, title="Account Opening Policy", doc_type="policy", file_name="v1.pdf", effective_date=date(2023, 1, 1))
    create_document(conn, title="Account Opening Policy", doc_type="policy", file_name="v2.pdf", effective_date=date(2024, 6, 1))

    current = current_documents(conn)

    assert len(current) == 1
    assert current[0].file_name == "v2.pdf"


def test_documents_of_different_types_are_never_grouped_as_versions(conn):
    create_document(conn, title="Account Opening", doc_type="policy", file_name="policy.pdf", effective_date=date(2024, 1, 1))
    create_document(conn, title="Account Opening", doc_type="procedure", file_name="procedure.pdf", effective_date=date(2023, 1, 1))

    current = current_documents(conn)

    assert {c.file_name for c in current} == {"policy.pdf", "procedure.pdf"}


def test_unrelated_documents_are_each_their_own_current_version(conn):
    create_document(conn, title="Policy A", doc_type="policy", file_name="a.pdf")
    create_document(conn, title="Policy B", doc_type="policy", file_name="b.pdf")

    current = current_documents(conn)

    assert {c.file_name for c in current} == {"a.pdf", "b.pdf"}


# --- version number stops a stale edit ---


def test_editing_with_the_current_version_succeeds(conn):
    card = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf")

    update_document_card(conn, card.id, expected_version=1, set_sql="title = %(title)s", set_params={"title": "New Title"})

    assert get_document(conn, card.id).title == "New Title"


def test_editing_with_a_stale_version_is_refused(conn):
    card = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf")
    update_document_card(conn, card.id, expected_version=1, set_sql="title = %(title)s", set_params={"title": "First Edit"})

    with pytest.raises(VersionConflict):
        update_document_card(conn, card.id, expected_version=1, set_sql="title = %(title)s", set_params={"title": "Stale Edit"})

    assert get_document(conn, card.id).title == "First Edit"


# --- step 3.3's needs: idempotent doc_id, fingerprint dedup, replace lookup ---


def test_create_document_with_an_explicit_id_is_idempotent(conn):
    """A retried upload job re-creates the same card safely -- no
    duplicate row, the first attempt's data wins."""
    first = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf", doc_id="fixed-id")
    second = create_document(conn, title="Different Title", doc_type="policy", file_name="p.pdf", doc_id="fixed-id")

    assert second.title == "Something"  # the original row, untouched
    assert len(list_documents(conn)) == 1


def test_fingerprint_is_stored_and_retrievable(conn):
    card = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf", fingerprint="abc123")

    assert get_document(conn, card.id).fingerprint == "abc123"


def test_a_new_card_is_not_indexed_yet(conn):
    card = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf")

    assert card.indexed is False
    assert card.fact_ids == ()


def test_mark_indexed_records_the_fact_ids(conn):
    card = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf")

    mark_indexed(conn, card.id, ["f_1", "f_2"])

    updated = get_document(conn, card.id)
    assert updated.indexed is True
    assert updated.fact_ids == ("f_1", "f_2")


def test_find_completed_by_fingerprint_only_matches_an_indexed_document(conn):
    card = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf", fingerprint="abc")

    assert find_completed_by_fingerprint(conn, "abc") is None  # not indexed yet

    mark_indexed(conn, card.id, ["f_1"])

    found = find_completed_by_fingerprint(conn, "abc")
    assert found is not None
    assert found.id == card.id


def test_find_completed_by_fingerprint_excludes_its_own_id(conn):
    """A retried job's own (not-yet-indexed) row must never match itself
    as if it were a pre-existing duplicate."""
    card = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf", doc_id="job-1", fingerprint="abc")
    mark_indexed(conn, card.id, ["f_1"])

    assert find_completed_by_fingerprint(conn, "abc", exclude_id="job-1") is None


def test_find_previous_completed_version_finds_the_currently_indexed_one(conn):
    old = create_document(conn, title="Account Policy", doc_type="policy", file_name="v1.pdf", effective_date=date(2023, 1, 1))
    mark_indexed(conn, old.id, ["f_old"])
    new = create_document(conn, title="Account Policy", doc_type="policy", file_name="v2.pdf", effective_date=date(2024, 1, 1))

    previous = find_previous_completed_version(conn, "Account Policy", "policy", exclude_id=new.id)

    assert previous is not None
    assert previous.id == old.id


def test_find_previous_completed_version_ignores_an_unindexed_earlier_attempt(conn):
    """A failed/never-finished earlier upload must not be treated as "the
    version to replace" -- only a fully indexed one counts."""
    create_document(conn, title="Account Policy", doc_type="policy", file_name="v1.pdf")  # never indexed
    new = create_document(conn, title="Account Policy", doc_type="policy", file_name="v2.pdf")

    assert find_previous_completed_version(conn, "Account Policy", "policy", exclude_id=new.id) is None


def test_delete_document_row_removes_it(conn):
    card = create_document(conn, title="Something", doc_type="policy", file_name="p.pdf")

    delete_document_row(conn, card.id)

    with pytest.raises(KeyError):
        get_document(conn, card.id)
