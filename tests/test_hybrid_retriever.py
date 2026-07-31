"""Hybrid retriever tests."""
from __future__ import annotations

from contracts.models import Confidence, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from online.retrieval.hybrid import retrieve


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
    """The asymmetric BGE prefix must apply to the query text — if
    the retriever accidentally called encode_passages on the query, this
    would go unnoticed with a real model but is directly observable here."""
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
