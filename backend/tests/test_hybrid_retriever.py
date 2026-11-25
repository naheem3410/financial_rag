"""Hybrid retriever tests."""
from __future__ import annotations

import pytest

from contracts.models import Confidence, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from offline.indexing.dense_index_faiss import PersistentDenseIndex
from online.retrieval.hybrid import TooManyVectorsMissing, retrieve


def _make_proposition(proposition_id: str, text: str) -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="Acme Corp", filing_type="10-Q", filing_date="2024-11-05",
            section="MD&A", page=34, source_uri="acme_10q.pdf",
        ),
    )


def test_empty_candidates_returns_empty():
    embedder = FakeEmbedder(dimension=2)

    assert retrieve("operating margin", [], embedder) == []


def test_every_candidate_gets_a_dense_and_sparse_score():
    candidates = [
        _make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."),
        _make_proposition("p_2", "Acme Corp net sales for Q3 FY2024 were $8.5 billion."),
    ]
    embedder = FakeEmbedder(dimension=8)

    results = retrieve("operating margin", candidates, embedder)

    assert len(results) == 2
    for r in results:
        assert isinstance(r.dense_score, float)
        assert isinstance(r.sparse_score, float)


def test_dense_score_uses_query_encoding_not_passage_encoding():
    """Catches a swapped encode_query/encode_passages call, which a real
    model would mask but the fake embedder makes obvious."""
    candidates = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]
    embedder = FakeEmbedder(dimension=8)

    retrieve("operating margin", candidates, embedder)

    assert embedder.query_calls == ["operating margin"]
    assert embedder.passage_calls == [candidates[0].text]


def test_sparse_score_ranks_matching_document_higher():
    candidates = [
        _make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."),
        _make_proposition("p_2", "Beta Inc net sales for Q3 FY2024 were $2.1 billion."),
    ]
    embedder = FakeEmbedder(dimension=8)

    results = retrieve("Acme operating margin", candidates, embedder)

    by_id = {r.proposition.proposition_id: r for r in results}
    assert by_id["p_1"].sparse_score > by_id["p_2"].sparse_score


def test_dense_score_reflects_controlled_vector_similarity():
    candidates = [
        _make_proposition("p_close", "close passage"),
        _make_proposition("p_far", "far passage"),
    ]
    embedder = FakeEmbedder(
        dimension=2,
        vectors={
            "the query": [1.0, 0.0],
            "close passage": [0.99, 0.14],
            "far passage": [0.0, 1.0],
        },
    )

    results = retrieve("the query", candidates, embedder)

    by_id = {r.proposition.proposition_id: r for r in results}
    assert by_id["p_close"].dense_score > by_id["p_far"].dense_score


# --- step 1.2: stored vectors instead of re-embedding (known issues 2/8, decisions 3/13) ---


def test_stored_vector_path_never_calls_encode_passages():
    candidates = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]
    embedder = FakeEmbedder(dimension=2, vectors={"the query": [1.0, 0.0]})
    dense_index = PersistentDenseIndex(dimension=2)
    dense_index.upsert("p_1", [1.0, 0.0])

    retrieve("the query", candidates, embedder, dense_index=dense_index)

    assert embedder.passage_calls == []  # the whole point of this step
    assert embedder.query_calls == ["the query"]  # the query still gets embedded


def test_stored_vector_path_gives_the_same_dense_scores_as_re_embedding():
    candidates = [
        _make_proposition("p_close", "close passage"),
        _make_proposition("p_far", "far passage"),
    ]
    shared_vectors = {
        "the query": [1.0, 0.0],
        "close passage": [0.99, 0.14],
        "far passage": [0.0, 1.0],
    }
    embedder = FakeEmbedder(dimension=2, vectors=shared_vectors)
    dense_index = PersistentDenseIndex(dimension=2)
    dense_index.upsert("p_close", shared_vectors["close passage"])
    dense_index.upsert("p_far", shared_vectors["far passage"])

    old_way = retrieve("the query", candidates, embedder)
    new_way = retrieve("the query", candidates, embedder, dense_index=dense_index)

    old_by_id = {r.proposition.proposition_id: r.dense_score for r in old_way}
    new_by_id = {r.proposition.proposition_id: r.dense_score for r in new_way}
    assert old_by_id["p_close"] == pytest.approx(new_by_id["p_close"], abs=1e-5)
    assert old_by_id["p_far"] == pytest.approx(new_by_id["p_far"], abs=1e-5)


def test_a_candidate_missing_from_the_dense_index_is_skipped_not_scored_wrong():
    candidates = [
        _make_proposition("p_1", "has a vector"),
        _make_proposition("p_2", "no vector yet"),
    ]
    embedder = FakeEmbedder(dimension=2, vectors={"the query": [1.0, 0.0]})
    dense_index = PersistentDenseIndex(dimension=2)
    dense_index.upsert("p_1", [1.0, 0.0])
    # p_2 deliberately never added

    results = retrieve("the query", candidates, embedder, dense_index=dense_index)

    assert [r.proposition.proposition_id for r in results] == ["p_1"]


def test_more_than_half_missing_vectors_raises_instead_of_guessing():
    candidates = [
        _make_proposition("p_1", "no vector"),
        _make_proposition("p_2", "no vector"),
        _make_proposition("p_3", "has a vector"),
    ]
    embedder = FakeEmbedder(dimension=2, vectors={"the query": [1.0, 0.0]})
    dense_index = PersistentDenseIndex(dimension=2)
    dense_index.upsert("p_3", [1.0, 0.0])

    with pytest.raises(TooManyVectorsMissing) as exc_info:
        retrieve("the query", candidates, embedder, dense_index=dense_index)

    assert set(exc_info.value.missing_ids) == {"p_1", "p_2"}


def test_exactly_half_missing_is_still_allowed_only_over_half_refuses():
    candidates = [
        _make_proposition("p_1", "no vector"),
        _make_proposition("p_2", "has a vector"),
    ]
    embedder = FakeEmbedder(dimension=2, vectors={"the query": [1.0, 0.0]})
    dense_index = PersistentDenseIndex(dimension=2)
    dense_index.upsert("p_2", [1.0, 0.0])

    results = retrieve("the query", candidates, embedder, dense_index=dense_index)  # does not raise

    assert [r.proposition.proposition_id for r in results] == ["p_2"]


def test_all_candidates_missing_from_the_dense_index_raises():
    candidates = [_make_proposition("p_1", "no vector"), _make_proposition("p_2", "no vector")]
    embedder = FakeEmbedder(dimension=2, vectors={"the query": [1.0, 0.0]})
    dense_index = PersistentDenseIndex(dimension=2)

    with pytest.raises(TooManyVectorsMissing):
        retrieve("the query", candidates, embedder, dense_index=dense_index)


def test_empty_candidates_with_a_dense_index_still_returns_empty():
    embedder = FakeEmbedder(dimension=2)
    dense_index = PersistentDenseIndex(dimension=2)

    assert retrieve("the query", [], embedder, dense_index=dense_index) == []
