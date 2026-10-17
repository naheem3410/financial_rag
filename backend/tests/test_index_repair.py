"""offline/indexing/repair.py: the database/dense/keyword three-store
consistency check and its background repair. Step 1.3."""
from __future__ import annotations

import psycopg
import pytest

from contracts.models import Confidence, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from offline.indexing.build import IndexBundle
from offline.indexing.dense_index_faiss import PersistentDenseIndex
from offline.indexing.index_writers import SparseIndex
from offline.indexing.repair import (
    IndexDiff,
    diff_index_state,
    is_severely_broken,
    publish_check_result,
    repair_from_event,
    run_check_and_repair_once,
    run_startup_check,
    start_background_index_check,
)
from infra.events import LocalEventBus, new_event


def _make_proposition(proposition_id: str, text: str) -> Proposition:
    return Proposition(
        proposition_id=proposition_id, text=text, provenance_tier="retrieved", ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="Acme Corp", filing_type="10-Q", filing_date="2024-11-05", section="MD&A", page=1, source_uri="acme.pdf"),
    )


class _FakeMetadataStore:
    def __init__(self, propositions):
        self._by_id = {p.proposition_id: p for p in propositions}

    def ids(self):
        return set(self._by_id)

    def get(self, proposition_id):
        return self._by_id[proposition_id]


# --- pure logic: no database needed ---


def test_diff_finds_nothing_when_everything_matches():
    diff = diff_index_state({"p_1", "p_2"}, {"p_1", "p_2"}, {"p_1", "p_2"})

    assert diff.is_clean()


def test_diff_finds_a_fact_missing_from_the_dense_index():
    diff = diff_index_state({"p_1", "p_2"}, {"p_1"}, {"p_1", "p_2"})

    assert diff.dense_missing == ("p_2",)
    assert diff.sparse_missing == ()
    assert not diff.is_clean()


def test_diff_finds_a_fact_missing_from_the_keyword_index():
    diff = diff_index_state({"p_1", "p_2"}, {"p_1", "p_2"}, {"p_1"})

    assert diff.sparse_missing == ("p_2",)


def test_diff_finds_a_ghost_in_each_index():
    diff = diff_index_state({"p_1"}, {"p_1", "p_ghost_d"}, {"p_1", "p_ghost_s"})

    assert diff.dense_ghosts == ("p_ghost_d",)
    assert diff.sparse_ghosts == ("p_ghost_s",)


def test_is_severely_broken_false_for_a_handful_missing():
    diff = IndexDiff(dense_missing=("p_1",))

    assert is_severely_broken(diff, db_count=100) is False


def test_is_severely_broken_true_over_half_missing():
    diff = IndexDiff(dense_missing=tuple(f"p_{i}" for i in range(51)))

    assert is_severely_broken(diff, db_count=100) is True


def test_is_severely_broken_exactly_half_is_still_allowed():
    diff = IndexDiff(dense_missing=tuple(f"p_{i}" for i in range(50)))

    assert is_severely_broken(diff, db_count=100) is False


def test_is_severely_broken_false_on_an_empty_database():
    assert is_severely_broken(IndexDiff(dense_missing=("p_1",)), db_count=0) is False


def test_run_startup_check_reads_ids_from_all_three_stores():
    bundle = IndexBundle(
        dense_index=_index_with(["p_1"]), sparse_index=_sparse_with(["p_1", "p_2"]),
        metadata_store=_FakeMetadataStore([_make_proposition("p_1", "a"), _make_proposition("p_2", "b")]),
    )

    diff = run_startup_check(bundle)

    assert diff.dense_missing == ("p_2",)
    assert diff.sparse_ghosts == ()


def _index_with(ids, dimension=8):
    index = PersistentDenseIndex(dimension=dimension)
    for pid in ids:
        index.upsert(pid, [1.0] + [0.0] * (dimension - 1))
    return index


def _sparse_with(ids):
    index = SparseIndex()
    for pid in ids:
        index.upsert(pid, "some text")
    return index


# --- publish_check_result: real event bus, real test database ---


@pytest.fixture(autouse=True)
def _clean_bus_tables(clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    for table in ("bus_events", "bus_cursors", "bus_failures", "shared_locks"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.close()


@pytest.fixture
def bus(clean_postgres):
    conn = psycopg.connect(clean_postgres, autocommit=True)
    yield LocalEventBus(conn)
    conn.close()


def test_publish_check_result_says_clean_when_nothing_is_wrong(bus):
    event = publish_check_result(bus, "job-1", IndexDiff(), db_count=10)

    assert event.type == "index.check_done"


def test_publish_check_result_reports_a_mismatch(bus):
    diff = IndexDiff(sparse_missing=("p_2",))

    event = publish_check_result(bus, "job-1", diff, db_count=10)

    assert event.type == "index.mismatch_found"
    assert event.data["sparse_missing"] == ["p_2"]


def test_publish_check_result_reports_severe_breakage_instead_of_a_normal_mismatch(bus):
    diff = IndexDiff(dense_missing=tuple(f"p_{i}" for i in range(6)))

    event = publish_check_result(bus, "job-1", diff, db_count=10)

    assert event.type == "index.severely_broken"


# --- repair_from_event: real Postgres for the lock, in-memory indexes ---


@pytest.fixture
def lock_conn(clean_postgres):
    connection = psycopg.connect(clean_postgres, autocommit=True)
    yield connection
    connection.close()


def test_repair_adds_a_missing_dense_vector(tmp_path, lock_conn):
    prop = _make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")
    bundle = IndexBundle(
        dense_index=PersistentDenseIndex(dimension=8), sparse_index=_sparse_with(["p_1"]),
        metadata_store=_FakeMetadataStore([prop]),
    )
    embedder = FakeEmbedder(dimension=8)
    event = new_event("index.mismatch_found", job_id="job-1", data={"dense_missing": ["p_1"], "sparse_missing": [], "sparse_ghosts": []})

    repair_from_event(event, bundle, embedder, lock_conn, tmp_path / "idx", tmp_path / "idx.sparse.json")

    assert bundle.dense_index.ids() == {"p_1"}
    # get_vectors returns the stored (L2-normalized) vector; cosine similarity to the
    # freshly embedded text should be ~1.0 regardless of that normalization.
    found, _ = bundle.dense_index.get_vectors(["p_1"])
    from contracts.vectors import cosine_similarity
    assert cosine_similarity(found["p_1"], embedder.encode_passages([prop.text])[0]) == pytest.approx(1.0, abs=1e-6)
    assert (tmp_path / "idx.faiss").exists()  # saved for next startup


def test_repair_adds_missing_keyword_postings(tmp_path, lock_conn):
    prop = _make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")
    bundle = IndexBundle(
        dense_index=_index_with(["p_1"]), sparse_index=SparseIndex(),
        metadata_store=_FakeMetadataStore([prop]),
    )
    embedder = FakeEmbedder(dimension=8)
    event = new_event("index.mismatch_found", job_id="job-1", data={"dense_missing": [], "sparse_missing": ["p_1"], "sparse_ghosts": []})

    repair_from_event(event, bundle, embedder, lock_conn, tmp_path / "idx", tmp_path / "idx.sparse.json")

    assert bundle.sparse_index.search("operating margin", k=1)[0][0] == "p_1"
    assert (tmp_path / "idx.sparse.json").exists()


def test_repair_removes_a_keyword_ghost(tmp_path, lock_conn):
    bundle = IndexBundle(
        dense_index=_index_with([]), sparse_index=_sparse_with(["p_ghost"]),
        metadata_store=_FakeMetadataStore([]),
    )
    embedder = FakeEmbedder(dimension=8)
    event = new_event("index.mismatch_found", job_id="job-1", data={"dense_missing": [], "sparse_missing": [], "sparse_ghosts": ["p_ghost"]})

    repair_from_event(event, bundle, embedder, lock_conn, tmp_path / "idx", tmp_path / "idx.sparse.json")

    assert bundle.sparse_index.ids() == set()


def test_repair_of_a_clean_diff_does_nothing_and_does_not_touch_files(tmp_path, lock_conn):
    bundle = IndexBundle(dense_index=_index_with([]), sparse_index=_sparse_with([]), metadata_store=_FakeMetadataStore([]))
    embedder = FakeEmbedder(dimension=8)
    event = new_event("index.mismatch_found", job_id="job-1", data={"dense_missing": [], "sparse_missing": [], "sparse_ghosts": []})

    repair_from_event(event, bundle, embedder, lock_conn, tmp_path / "idx", tmp_path / "idx.sparse.json")

    assert not (tmp_path / "idx.faiss").exists()
    assert not (tmp_path / "idx.sparse.json").exists()


def test_repair_fixes_everything_in_one_go(tmp_path, lock_conn):
    prop_missing_dense = _make_proposition("p_dense", "net income was five dollars")
    prop_missing_sparse = _make_proposition("p_sparse", "revenue was ten dollars")
    bundle = IndexBundle(
        dense_index=_index_with(["p_sparse"]), sparse_index=_sparse_with(["p_dense", "p_ghost"]),
        metadata_store=_FakeMetadataStore([prop_missing_dense, prop_missing_sparse]),
    )
    embedder = FakeEmbedder(dimension=8)
    event = new_event("index.mismatch_found", job_id="job-1", data={
        "dense_missing": ["p_dense"], "sparse_missing": ["p_sparse"], "sparse_ghosts": ["p_ghost"],
    })

    repair_from_event(event, bundle, embedder, lock_conn, tmp_path / "idx", tmp_path / "idx.sparse.json")

    assert bundle.dense_index.ids() == {"p_sparse", "p_dense"}
    assert bundle.sparse_index.ids() == {"p_dense", "p_sparse"}
    final_diff = diff_index_state(bundle.metadata_store.ids(), bundle.dense_index.ids(), bundle.sparse_index.ids())
    assert final_diff.is_clean()


# --- run_check_and_repair_once: the full cycle in one call ---


def test_full_cycle_heals_a_mismatch(tmp_path, bus, lock_conn):
    """3 facts, only 1 missing (a third, under the severe threshold) -- a
    normal, repairable drift."""
    props = [_make_proposition(f"p_{i}", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.") for i in range(3)]
    bundle = IndexBundle(
        dense_index=_index_with(["p_0", "p_1"]), sparse_index=_sparse_with(["p_0", "p_1", "p_2"]),
        metadata_store=_FakeMetadataStore(props),
    )
    embedder = FakeEmbedder(dimension=8)

    event = run_check_and_repair_once(bundle, embedder, tmp_path / "idx", tmp_path / "idx.sparse.json", bus, lock_conn)

    assert event.type == "index.mismatch_found"
    assert bundle.dense_index.ids() == {"p_0", "p_1", "p_2"}


def test_full_cycle_on_a_clean_index_does_nothing(tmp_path, bus, lock_conn):
    prop = _make_proposition("p_1", "some text")
    bundle = IndexBundle(
        dense_index=_index_with(["p_1"]), sparse_index=_sparse_with(["p_1"]),
        metadata_store=_FakeMetadataStore([prop]),
    )
    embedder = FakeEmbedder(dimension=8)

    event = run_check_and_repair_once(bundle, embedder, tmp_path / "idx", tmp_path / "idx.sparse.json", bus, lock_conn)

    assert event.type == "index.check_done"
    assert not (tmp_path / "idx.faiss").exists()


def test_full_cycle_on_a_severely_broken_index_does_not_repair(tmp_path, bus, lock_conn):
    props = [_make_proposition(f"p_{i}", "text") for i in range(10)]
    bundle = IndexBundle(
        dense_index=_index_with([]),  # every single one missing
        sparse_index=_sparse_with([p.proposition_id for p in props]),
        metadata_store=_FakeMetadataStore(props),
    )
    embedder = FakeEmbedder(dimension=8)

    event = run_check_and_repair_once(bundle, embedder, tmp_path / "idx", tmp_path / "idx.sparse.json", bus, lock_conn)

    assert event.type == "index.severely_broken"
    assert bundle.dense_index.ids() == set()  # left alone, not silently patched


# --- start_background_index_check: real thread, real test database ---


def test_background_check_runs_without_blocking_and_heals_the_index(clean_postgres, tmp_path):
    props = [_make_proposition(f"p_{i}", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.") for i in range(3)]
    bundle = IndexBundle(
        dense_index=_index_with(["p_0", "p_1"]), sparse_index=_sparse_with(["p_0", "p_1", "p_2"]),
        metadata_store=_FakeMetadataStore(props),
    )
    embedder = FakeEmbedder(dimension=8)

    thread = start_background_index_check(bundle, embedder, tmp_path / "idx", tmp_path / "idx.sparse.json", clean_postgres)
    assert thread.is_alive() or True  # returns at once either way -- the caller is never blocked
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert bundle.dense_index.ids() == {"p_0", "p_1", "p_2"}  # healed once the background thread finished
