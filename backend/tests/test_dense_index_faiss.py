"""PersistentDenseIndex (FAISS) tests."""
from __future__ import annotations

import pytest

from offline.indexing.dense_index_faiss import PersistentDenseIndex


def test_upsert_rejects_wrong_dimension_vector():
    index = PersistentDenseIndex(dimension=8)

    with pytest.raises(ValueError, match="dimension mismatch"):
        index.upsert("p_1", [0.1, 0.2, 0.3])


def test_upsert_rejects_duplicate_proposition_id():
    index = PersistentDenseIndex(dimension=3)
    index.upsert("p_1", [1.0, 0.0, 0.0])

    with pytest.raises(ValueError, match="already present"):
        index.upsert("p_1", [0.0, 1.0, 0.0])


def test_get_vectors_reads_back_what_was_stored():
    index = PersistentDenseIndex(dimension=2)
    index.upsert("p_1", [1.0, 0.0])
    index.upsert("p_2", [0.0, 1.0])

    found, missing = index.get_vectors(["p_1", "p_2"])

    assert missing == []
    assert found["p_1"] == pytest.approx([1.0, 0.0])
    assert found["p_2"] == pytest.approx([0.0, 1.0])


def test_get_vectors_l2_normalizes_like_upsert_did():
    index = PersistentDenseIndex(dimension=2)
    index.upsert("p_1", [3.0, 4.0])  # norm 5

    found, _ = index.get_vectors(["p_1"])

    assert found["p_1"] == pytest.approx([0.6, 0.8])


def test_get_vectors_reports_ids_not_in_the_index(tmp_path):
    index = PersistentDenseIndex(dimension=2)
    index.upsert("p_1", [1.0, 0.0])

    found, missing = index.get_vectors(["p_1", "p_ghost"])

    assert set(found) == {"p_1"}
    assert missing == ["p_ghost"]


def test_get_vectors_on_an_empty_index_reports_everything_missing():
    index = PersistentDenseIndex(dimension=2)

    found, missing = index.get_vectors(["p_1", "p_2"])

    assert found == {}
    assert missing == ["p_1", "p_2"]


def test_get_vectors_survives_a_save_and_load_round_trip(tmp_path):
    index = PersistentDenseIndex(dimension=2)
    index.upsert("p_1", [1.0, 0.0])
    path = tmp_path / "idx"
    index.save(path)

    reloaded = PersistentDenseIndex.load(path, dimension=2)
    found, missing = reloaded.get_vectors(["p_1"])

    assert missing == []
    assert found["p_1"] == pytest.approx([1.0, 0.0])


def test_search_ranks_closest_vector_first():
    index = PersistentDenseIndex(dimension=2)
    index.upsert("p_close", [1.0, 0.0])
    index.upsert("p_far", [0.0, 1.0])

    results = index.search([0.9, 0.1], k=2)

    assert results[0][0] == "p_close"


def test_save_and_load_roundtrip_preserves_ids_and_search(tmp_path):
    index = PersistentDenseIndex(dimension=3)
    index.upsert("p_1", [1.0, 0.0, 0.0])
    index.upsert("p_2", [0.0, 1.0, 0.0])
    path = tmp_path / "index"

    index.save(path)
    reloaded = PersistentDenseIndex.load(path, dimension=3)

    assert reloaded.ids() == {"p_1", "p_2"}
    assert reloaded.search([1.0, 0.0, 0.0], k=1)[0][0] == "p_1"


def test_save_creates_both_sidecar_files(tmp_path):
    index = PersistentDenseIndex(dimension=2)
    index.upsert("p_1", [1.0, 0.0])
    path = tmp_path / "myindex"

    index.save(path)

    assert (tmp_path / "myindex.faiss").exists()
    assert (tmp_path / "myindex.ids.json").exists()
