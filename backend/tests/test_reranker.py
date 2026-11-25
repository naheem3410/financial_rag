"""Cross-encoder reranker tests. Loads the real pinned BAAI/bge-reranker-base checkpoint.
What matters is whether it separates relevant from irrelevant passages, not just that sorting works.
"""
from __future__ import annotations

from contracts.models import Confidence, Period, Proposition, PropositionMetadata, RequestContext, SubQuery
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


def test_render_query_text_resolves_npce_placeholder_to_the_real_name():
    """Decision 32 shape: with NPCE on, sq.entity is an opaque placeholder
    (e.g. <ENT_3>), never a real company name -- but the passage text the
    cross-encoder sees always has the real name, since the index never
    stores placeholders. Without resolving it here, the cross-encoder
    loses the entity-match signal entirely."""
    sq = SubQuery(
        sub_query_id="sq_1", entity="<ENT_3>", metric="operating_margin",
        period=Period(fiscal_year=2024, quarter=3), period_label="Q3 FY2024",
    )
    ctx = RequestContext(request_id="r", config_hash="h", placeholder_map={"<ENT_3>": "Acme Corp"})

    text = render_query_text(sq, ctx)

    assert text == "Acme Corp operating margin Q3 FY2024"


def test_render_query_text_without_ctx_or_unmapped_placeholder_uses_entity_as_is():
    sq = SubQuery(
        sub_query_id="sq_1", entity="<ENT_9>", metric="operating_margin",
        period=Period(fiscal_year=2024, quarter=3), period_label="Q3 FY2024",
    )

    assert render_query_text(sq) == "<ENT_9> operating margin Q3 FY2024"
    empty_ctx = RequestContext(request_id="r", config_hash="h")
    assert render_query_text(sq, empty_ctx) == "<ENT_9> operating margin Q3 FY2024"


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


def test_real_model_with_npce_placeholder_still_ranks_the_real_headline_above_a_narrower_note():
    """Decision 32: real trace against financial_rag_v2 found the reranker
    ranking Boeing's supply-chain-financing note above the real $10,200M
    accounts-payable figure, because render_query_text was sent the raw
    NPCE placeholder (<ENT_11>) instead of 'The Boeing Company' -- the
    passage text has the real name, so the entity-match signal was lost.
    Passing ctx restores it; this reproduces the shape with the real
    pinned model, not a mock."""
    headline = _make_proposition(
        "p_headline", "The Boeing Company had accounts payable of $10,200 million at December 31, 2022."
    )
    narrower_note = _make_proposition(
        "p_note",
        "At December 31, 2022, Accounts payable included $2.5 billion payable to suppliers who have "
        "elected to participate in supply chain financing programs.",
    )
    sq = SubQuery(
        sub_query_id="sq_boeing", entity="<ENT_11>", metric="accounts_payable",
        period=Period(fiscal_year=2022), period_label="FY2022",
    )
    ctx = RequestContext(request_id="r", config_hash="h", placeholder_map={"<ENT_11>": "The Boeing Company"})
    reranker = CrossEncoderReranker()

    result = reranker.rerank(sq, [narrower_note, headline], k=2, ctx=ctx)

    assert [p.proposition_id for p in result] == ["p_headline", "p_note"]


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
