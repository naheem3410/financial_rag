"""Cross-encoder reranker tests. Loads the real pinned BAAI/bge-reranker-base checkpoint.
What matters is whether it separates relevant from irrelevant passages, not just that sorting works.
"""
from __future__ import annotations

from contracts.models import Confidence, Period, Proposition, PropositionMetadata, SubQuery
from online.retrieval.reranker import MODEL_NAME, MODEL_REVISION, CrossEncoderReranker, render_query_text


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


def _sub_query() -> SubQuery:
    return SubQuery(
        sub_query_id="sq_1", entity="Acme Corp", metric="operating_margin",
        period=Period(fiscal_year=2024, quarter=3), period_label="Q3 FY2024",
    )


def test_render_query_text_combines_resolved_slots():
    text = render_query_text(_sub_query())

    assert text == "Acme Corp operating margin Q3 FY2024"


def test_reports_pinned_identity_without_loading_the_model():
    reranker = CrossEncoderReranker()

    assert reranker.model_name == MODEL_NAME
    assert reranker.revision == MODEL_REVISION
    assert reranker._model is None


def test_empty_candidates_returns_empty_without_loading_the_model():
    reranker = CrossEncoderReranker()

    result = reranker.rerank(_sub_query(), [], k=5)

    assert result == []
    assert reranker._model is None


def test_real_model_ranks_the_relevant_passage_above_an_unrelated_one():
    relevant = _make_proposition("p_relevant", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")
    unrelated = _make_proposition(
        "p_unrelated", "The Eiffel Tower is a wrought-iron lattice tower in Paris, France."
    )
    reranker = CrossEncoderReranker()

    result = reranker.rerank(_sub_query(), [unrelated, relevant], k=2)

    assert [p.proposition_id for p in result] == ["p_relevant", "p_unrelated"]


def test_real_model_respects_k_truncation():
    props = [
        _make_proposition("p_relevant", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."),
        _make_proposition("p_unrelated_1", "The Eiffel Tower is a wrought-iron lattice tower in Paris, France."),
        _make_proposition("p_unrelated_2", "Photosynthesis converts light energy into chemical energy in plants."),
    ]
    reranker = CrossEncoderReranker()

    result = reranker.rerank(_sub_query(), props, k=1)

    assert len(result) == 1
    assert result[0].proposition_id == "p_relevant"
