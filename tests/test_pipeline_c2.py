"""C2 end-to-end pipeline wiring tests.
Uses fake dense/sparse/metadata stores, a fake reranker, and fake FFF/
Generator/CoVe LLM clients (no real FAISS/Postgres/models/API calls) — these
test that the full query -> answer -> CoVe-verified -> citation wiring is
correct, not the already-tested pieces (retrieval, FFF guard, Generator
guard, CoVe orchestrator internals, citation binding, abstention) it
composes. See tests/test_pipeline_c1.py for the C1 sibling these mirror.
"""
from __future__ import annotations

from config.schema import ExperimentConfig
from contracts.models import Confidence, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from online.pipeline_c2 import answer_question


def _make_proposition(proposition_id: str, source_uri: str, page: int, text: str, company: str = "3M Company") -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company=company, filing_type="10-K", filing_date="2018-12-31",
            section="", page=page, source_uri=source_uri,
        ),
    )


class _FakeDenseIndex:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query_vector, k):
        return self._hits[:k]


class _FakeSparseIndex:
    def search(self, query_text, k):
        return []


class _FakeMetadataStore:
    def __init__(self, propositions_by_id):
        self._by_id = propositions_by_id

    def get(self, proposition_id):
        return self._by_id[proposition_id]


class _FakeBundle:
    def __init__(self, dense_index, sparse_index, metadata_store):
        self.dense_index = dense_index
        self.sparse_index = sparse_index
        self.metadata_store = metadata_store


class _FakeReranker:
    def rerank(self, sq, props, k):
        return list(props[:k])


class _FakeLLMClient:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        return self._response


def _config():
    return ExperimentConfig.model_validate(
        {
            "experiment_id": "c2_cove",
            "enable_npce": False,
            "enable_cove": True,
            "retrieval": {"mode": "hybrid", "top_k": 20, "rerank_top_k": 5, "enable_reranker": True, "time_weighting": False},
            "indexing": {"granularity": "proposition"},
            "llm": {
                "defaults": {"temperature": 0.0, "structured_output": True},
                "call_sites": {
                    name: {"provider": "openai", "model": "m"}
                    for name in ("proposition_segmenter", "numeric_qualifier_resolver", "query_splitter", "fff_rewriter", "generator", "cove_verification")
                },
                "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
            },
            "corpus": {"pdf_dir": "data/pdfs", "required_docs_file": "data/metric_axis_required_docs.txt"},
        }
    )


def test_full_pipeline_produces_a_cove_verified_cited_answer():
    prop = _make_proposition("p_1", "3M_2018_10K", page=57, text="3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    bundle = _FakeBundle(_FakeDenseIndex([("p_1", 0.9)]), _FakeSparseIndex(), _FakeMetadataStore({"p_1": prop}))
    fff_client = _FakeLLMClient(
        {"reasoning": "...", "facts": [
            {"proposition_id": "p_1", "subject": "3M", "metric": "net_sales", "period_label": "FY2018", "value_text": "$32,765 million"},
        ]}
    )
    generator_client = _FakeLLMClient(
        {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "net_sales", "answer_value": "$32,765 million", "answer_unit": "millions"}],
            "answer_text": "3M's net sales were $32,765 million.",
            "used_fff_ids": ["f_placeholder"],
        }
    )

    from online.curation.fff_rewriter import rewrite_to_ffts
    real_ffts = rewrite_to_ffts("q", [prop], _FakeLLMClient(fff_client._response))
    generator_client._response["used_fff_ids"] = [real_ffts[0].fff_id]

    claim_extraction_client = _FakeLLMClient(
        {
            "reasoning": "...",
            "claims": [{
                "claim_id": "c_1", "claim_text": "3M's net sales were $32,765 million.",
                "entity": "3M Company", "metric": "net_sales", "period_label": "FY2018",
                "asserted_value": "32765", "asserted_unit": "USD", "scale": "millions",
                "is_numeric": True,
            }],
        }
    )
    independent_verification_client = _FakeLLMClient(
        {
            "reasoning": "...", "supported": True,
            "answer_value": "32765", "answer_unit": "USD",
            "period_label": "FY2018", "scale": "millions",
            "supporting_fff_ids": [real_ffts[0].fff_id],
        }
    )
    prose_comparison_client = _FakeLLMClient({"reasoning": "...", "verdict": "match"})

    result = answer_question(
        "What were 3M's net sales in FY2018?", bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), fff_rewriter_model="m", generator_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.abstained is False
    assert result.answerable is True
    assert result.answers[0].value == "$32,765 million"
    assert len(result.citations) == 1
    assert result.citations[0]["source_uri"] == "3M_2018_10K"
    assert result.cove_verification_model == "gpt-5.6-luna"
    assert len(result.verification_trace) == 1
    assert result.verification_trace[0]["verdict"] == "match"
    assert prose_comparison_client.calls == 0  # numeric claim -> numeric_match, never the prose LLM


def test_cove_catches_a_temporal_collision_and_forces_abstention():
    """The 0061 mechanism: the draft confidently states a FY2023
    figure, but the evidence CoVe's independent verification is executed
    against actually covers FY2022 -- a real fiscal-year mismatch. A working
    verifier flags this as a conflict, and conflict is non-`match`, so the
    abstention gate (any non-match claim is material) must abstain the
    whole answer rather than let the wrong-year figure through."""
    prop = _make_proposition("p_1", "3M_2022_10K", page=40, text="3M Company had revenue of $30.0 billion in the year ended December 31, 2022.")
    bundle = _FakeBundle(_FakeDenseIndex([("p_1", 0.9)]), _FakeSparseIndex(), _FakeMetadataStore({"p_1": prop}))
    fff_client = _FakeLLMClient(
        {"reasoning": "...", "facts": [
            {"proposition_id": "p_1", "subject": "3M", "metric": "revenue", "period_label": "FY2022", "value_text": "$30.0 billion"},
        ]}
    )
    generator_client = _FakeLLMClient(
        {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "revenue", "answer_value": "$30.0 billion", "answer_unit": "billions"}],
            "answer_text": "3M's revenue for FY2023 was $30.0 billion.",  # wrong year, generator blended it in
            "used_fff_ids": ["f_placeholder"],
        }
    )

    from online.curation.fff_rewriter import rewrite_to_ffts
    real_ffts = rewrite_to_ffts("q", [prop], _FakeLLMClient(fff_client._response))
    generator_client._response["used_fff_ids"] = [real_ffts[0].fff_id]

    claim_extraction_client = _FakeLLMClient(
        {
            "reasoning": "...",
            "claims": [{
                "claim_id": "c_1", "claim_text": "3M's revenue for FY2023 was $30.0 billion.",
                "entity": "3M Company", "metric": "revenue", "period_label": "FY2023",
                "asserted_value": "30.0", "asserted_unit": "USD", "scale": "billions",
                "is_numeric": True,
            }],
        }
    )
    independent_verification_client = _FakeLLMClient(
        {
            "reasoning": "the only evidence available is for FY2022, not FY2023",
            "supported": True,
            "answer_value": "30.0", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "billions",  # evidence's true period -- disagrees with the claim
            "supporting_fff_ids": [real_ffts[0].fff_id],
        }
    )
    prose_comparison_client = _FakeLLMClient({"reasoning": "...", "verdict": "match"})

    result = answer_question(
        "What was 3M's revenue in FY2023?", bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), fff_rewriter_model="m", generator_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.verification_trace[0]["verdict"] == "conflict"
    assert result.abstained is True
    assert "not supported" in result.abstention_message.lower()


def test_pipeline_abstains_when_no_propositions_are_retrieved_cove_never_invoked():
    bundle = _FakeBundle(_FakeDenseIndex([]), _FakeSparseIndex(), _FakeMetadataStore({}))
    fff_client = _FakeLLMClient({"reasoning": "...", "facts": []})
    generator_client = _FakeLLMClient({"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []})
    claim_extraction_client = _FakeLLMClient({"reasoning": "...", "claims": []})
    independent_verification_client = _FakeLLMClient({})
    prose_comparison_client = _FakeLLMClient({})

    result = answer_question(
        "What were 3M's net sales in FY2099?", bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), fff_rewriter_model="m", generator_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.abstained is True
    assert result.citations == []
    assert fff_client.calls == 0
    assert generator_client.calls == 0
    assert claim_extraction_client.calls == 0  # nothing to verify -- CoVe never invoked
