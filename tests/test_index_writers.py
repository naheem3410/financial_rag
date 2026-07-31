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
