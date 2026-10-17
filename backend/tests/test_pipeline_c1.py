"""C1 end-to-end pipeline wiring tests.
All fakes, no real FAISS/Postgres/API calls - checks the wiring, not the pieces it composes.
"""
from __future__ import annotations

from config.schema import ExperimentConfig
from contracts.models import Confidence, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from online.pipeline_c1 import answer_question


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


class _FakeFFFClient:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        return self._response


class _FakeGeneratorClient:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        return self._response


def _config():
    return ExperimentConfig.model_validate(
        {
            "experiment_id": "c1_baseline",
            "enable_npce": False,
            "enable_cove": False,
            "retrieval": {"mode": "hybrid", "top_k": 20, "rerank_top_k": 5, "enable_reranker": True, "time_weighting": False},
            "indexing": {"granularity": "proposition"},
            "llm": {
                "defaults": {"temperature": 0.0, "structured_output": True},
                "call_sites": {
                    name: {"provider": "openai", "model": "m"}
                    for name in ("proposition_segmenter", "numeric_qualifier_resolver", "query_splitter", "fff_rewriter", "generator", "cove_verification", "step_planner")
                },
                "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
            },
            "corpus": {"pdf_dir": "data/pdfs", "required_docs_file": "data/metric_axis_required_docs.txt"},
        }
    )


def test_full_pipeline_produces_a_cited_answer():
    prop = _make_proposition("p_1", "3M_2018_10K", page=57, text="3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    bundle = _FakeBundle(
        _FakeDenseIndex([("p_1", 0.9)]), _FakeSparseIndex(), _FakeMetadataStore({"p_1": prop})
    )
    fff_client = _FakeFFFClient(
        {"reasoning": "...", "facts": [
            {"proposition_id": "p_1", "subject": "3M", "metric": "net_sales", "period_label": "FY2018", "value_text": "$32,765 million"},
        ]}
    )
    generator_client = _FakeGeneratorClient(
        {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "net_sales", "answer_value": "$32,765 million", "answer_unit": "millions"}],
            "answer_text": "3M's net sales were $32,765 million.",
            "used_fff_ids": ["f_placeholder"],  # gets overwritten to the real fff_id below
        }
    )

    # fff_id is computed deterministically inside rewrite_to_ffts, so run that
    # for real first and patch the generator response to match its output.
    from online.curation.fff_rewriter import rewrite_to_ffts
    real_ffts = rewrite_to_ffts("q", [prop], _FakeFFFClient(fff_client._response))
    generator_client._response["used_fff_ids"] = [real_ffts[0].fff_id]

    result = answer_question(
        "What were 3M's net sales in FY2018?", bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        fff_client, generator_client, _config(), fff_rewriter_model="m", generator_model="m",
    )

    assert result.abstained is False
    assert result.answerable is True
    assert result.answers[0].value == "$32,765 million"
    assert len(result.citations) == 1
    assert result.citations[0]["source_uri"] == "3M_2018_10K"
    assert result.citations[0]["page"] == 57
    assert result.fff_rewriter_model == "m"
    assert result.generator_model == "m"


def test_pipeline_abstains_when_no_propositions_are_retrieved():
    bundle = _FakeBundle(_FakeDenseIndex([]), _FakeSparseIndex(), _FakeMetadataStore({}))
    fff_client = _FakeFFFClient({"reasoning": "...", "facts": []})
    generator_client = _FakeGeneratorClient({"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []})

    result = answer_question(
        "What were 3M's net sales in FY2099?", bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        fff_client, generator_client, _config(), fff_rewriter_model="m", generator_model="m",
    )

    assert result.abstained is True
    assert "not supported" in result.abstention_message.lower()
    assert result.citations == []
    assert fff_client.calls == 0  # no propositions -> FFF Rewriter never called
    assert generator_client.calls == 0  # no facts -> Generator never called


def test_pipeline_abstains_when_generator_says_not_answerable():
    prop = _make_proposition("p_1", "3M_2018_10K", page=57, text="3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    bundle = _FakeBundle(_FakeDenseIndex([("p_1", 0.9)]), _FakeSparseIndex(), _FakeMetadataStore({"p_1": prop}))
    fff_client = _FakeFFFClient(
        {"reasoning": "...", "facts": [
            {"proposition_id": "p_1", "subject": "3M", "metric": "net_sales", "period_label": "FY2018", "value_text": "$32,765 million"},
        ]}
    )
    generator_client = _FakeGeneratorClient(
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "The facts do not state this.", "used_fff_ids": []}
    )

    result = answer_question(
        "What was 3M's completely unrelated metric?", bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        fff_client, generator_client, _config(), fff_rewriter_model="m", generator_model="m",
    )

    assert result.abstained is True
    assert result.answerable is False
    assert result.citations == []
