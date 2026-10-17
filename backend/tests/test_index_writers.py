"""Index writers tests."""
from __future__ import annotations

import pytest

from contracts.models import Confidence, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from offline.indexing.index_writers import DenseIndex, IndexWriters


def _make_proposition(proposition_id: str, text: str) -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="Acme Corp",
            filing_type="10-Q",
            filing_date="2024-11-05",
            section="MD&A",
            page=34,
            source_uri="acme_10q_2024q3.pdf",
        ),
    )


class TestDenseIndexDimensionGuard:
    def test_upsert_rejects_wrong_dimension_vector(self):
        index = DenseIndex(dimension=8)

        with pytest.raises(ValueError, match="dimension mismatch"):
            index.upsert("p_1", [0.1, 0.2, 0.3])  # 3-d into an 8-d index

    def test_upsert_accepts_matching_dimension_vector(self):
        index = DenseIndex(dimension=3)

        index.upsert("p_1", [0.1, 0.2, 0.3])

        assert index.ids() == {"p_1"}


class TestIndexWritersThreeIndexInvariant:
    def test_write_populates_all_three_stores_with_identical_ids(self):
        embedder = FakeEmbedder(dimension=8)
        writers = IndexWriters(embedder=embedder)
        props = [
            _make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."),
            _make_proposition("p_2", "Acme Corp net sales for Q3 FY2024 were $8.5 billion."),
        ]

        writers.write(props)

        assert writers.dense_index.ids() == writers.sparse_index.ids() == writers.metadata_store.ids()
        assert writers.dense_index.ids() == {"p_1", "p_2"}

    def test_dense_index_dimension_matches_embedder(self):
        embedder = FakeEmbedder(dimension=16)
        writers = IndexWriters(embedder=embedder)

        assert writers.dense_index.dimension == 16

    def test_write_uses_passage_encoding(self):
        embedder = FakeEmbedder(dimension=8)
        writers = IndexWriters(embedder=embedder)
        props = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]

        writers.write(props)

        assert embedder.passage_calls == [props[0].text]
        assert embedder.query_calls == []

    def test_metadata_store_retrieves_the_full_proposition(self):
        embedder = FakeEmbedder(dimension=8)
        writers = IndexWriters(embedder=embedder)
        prop = _make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")

        writers.write([prop])

        assert writers.metadata_store.get("p_1") == prop

    def test_write_with_empty_propositions_is_a_no_op(self):
        embedder = FakeEmbedder(dimension=8)
        writers = IndexWriters(embedder=embedder)

        writers.write([])

        assert writers.dense_index.ids() == set()
        assert writers.sparse_index.ids() == set()
        assert writers.metadata_store.ids() == set()


class TestSparseIndexBM25:
    def test_search_ranks_matching_document_first(self):
        from offline.indexing.index_writers import SparseIndex

        index = SparseIndex()
        index.upsert("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")
        index.upsert("p_2", "Beta Inc net sales for Q3 FY2024 were $2.1 billion.")

        results = index.search("Acme operating margin", k=2)

        assert results[0][0] == "p_1"

    def test_upsert_is_idempotent_for_the_same_proposition_id(self):
        from offline.indexing.index_writers import SparseIndex

        index = SparseIndex()
        index.upsert("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")
        index.upsert("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")

        assert index.ids() == {"p_1"}

    def test_remove_drops_a_ghost_id(self):
        from offline.indexing.index_writers import SparseIndex

        index = SparseIndex()
        index.upsert("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")

        index.remove("p_1")

        assert index.ids() == set()

    def test_remove_of_an_id_never_added_is_a_no_op(self):
        from offline.indexing.index_writers import SparseIndex

        index = SparseIndex()
        index.upsert("p_1", "some text")

        index.remove("p_ghost")  # must not raise

        assert index.ids() == {"p_1"}

    def test_remove_then_a_later_upsert_of_the_same_id_works_normally(self):
        from offline.indexing.index_writers import SparseIndex

        index = SparseIndex()
        index.upsert("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")
        index.remove("p_1")
        index.upsert("p_1", "Beta Inc net sales for Q3 FY2024 were $2.1 billion.")

        results = index.search("Beta net sales", k=1)

        assert results[0][0] == "p_1"

    def test_no_temp_file_is_left_behind_after_save(self, tmp_path):
        from offline.indexing.index_writers import SparseIndex

        index = SparseIndex()
        index.upsert("p_1", "some text")
        path = tmp_path / "keyword_index.json"

        index.save(path)

        assert path.exists()
        assert list(tmp_path.glob("*.tmp")) == []


class TestSparseIndexSaveLoad:
    """Step 1.3: saved at build time, loaded at startup -- no re-tokenizing
    the facts' text, no trip to the database."""

    def test_load_gives_the_same_ranking_as_the_original(self, tmp_path):
        from offline.indexing.index_writers import SparseIndex

        index = SparseIndex()
        index.upsert("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")
        index.upsert("p_2", "Beta Inc net sales for Q3 FY2024 were $2.1 billion.")
        path = tmp_path / "keyword_index.json"
        index.save(path)

        reloaded = SparseIndex.load(path)

        assert reloaded.search("Acme operating margin", k=2) == index.search("Acme operating margin", k=2)
        assert reloaded.ids() == index.ids()

    def test_load_of_a_missing_file_is_a_clear_error(self, tmp_path):
        from offline.indexing.index_writers import SparseIndex

        with pytest.raises(FileNotFoundError):
            SparseIndex.load(tmp_path / "does_not_exist.json")

    def test_load_of_a_corrupt_file_is_a_clear_error(self, tmp_path):
        from offline.indexing.index_writers import SparseIndex, SparseIndexFormatError

        path = tmp_path / "keyword_index.json"
        path.write_text("not json at all {{{", encoding="utf-8")

        with pytest.raises(SparseIndexFormatError):
            SparseIndex.load(path)

    def test_load_refuses_an_older_format_version(self, tmp_path):
        import json

        from offline.indexing.index_writers import SparseIndex, SparseIndexFormatError

        path = tmp_path / "keyword_index.json"
        path.write_text(json.dumps({"version": 0, "k1": 1.5, "b": 0.75, "postings": {}, "doc_freq": {}}), encoding="utf-8")

        with pytest.raises(SparseIndexFormatError, match="different build"):
            SparseIndex.load(path)

    def test_save_then_load_preserves_bm25_parameters(self, tmp_path):
        from offline.indexing.index_writers import SparseIndex

        index = SparseIndex(k1=1.2, b=0.6)
        index.upsert("p_1", "some text")
        path = tmp_path / "keyword_index.json"
        index.save(path)

        reloaded = SparseIndex.load(path)

        assert reloaded._k1 == 1.2
        assert reloaded._b == 0.6
