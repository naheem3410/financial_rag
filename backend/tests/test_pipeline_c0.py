"""C0 end-to-end pipeline wiring tests (naive baseline, outside the 2x2 factorial).
All fakes, no real FAISS/Postgres/API calls - checks the wiring, not the pieces it composes.
"""
from __future__ import annotations

from config.schema import ExperimentConfig
from contracts.models import Confidence, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from online.pipeline_c0 import answer_question


def _chunk(cid: str, page: int, text: str) -> Proposition:
    return Proposition(
        proposition_id=cid,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="3M Company", filing_type="10-K", filing_date="2018-12-31",
            section="", page=page, source_uri="3M_2018_10K",
        ),
    )


class _FakeDenseIndex:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query_vector, k):
        return self._hits[:k]


class _FakeMetadataStore:
    def __init__(self, propositions_by_id):
        self._by_id = propositions_by_id

    def get(self, proposition_id):
        return self._by_id[proposition_id]


class _FakeBundle:
    def __init__(self, dense_index, metadata_store):
        self.dense_index = dense_index
        self.metadata_store = metadata_store


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
            "experiment_id": "c0_naive",
            "enable_npce": False,
            "enable_cove": False,
            "retrieval": {"mode": "naive_dense", "top_k": 20, "rerank_top_k": 5, "enable_reranker": False, "time_weighting": False},
            "indexing": {"granularity": "fixed_size"},
            "llm": {
                "defaults": {"temperature": 0.0, "structured_output": True},
                "call_sites": {
                    name: {"provider": "openai", "model": "m"}
                    for name in ("proposition_segmenter", "numeric_qualifier_resolver", "query_splitter", "fff_rewriter", "generator", "cove_verification", "step_planner")
                },
                "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
            },
            "corpus": {"pdf_dir": "data/pdfs", "required_docs_file": "data/required_docs.txt"},
        }
    )


def test_full_pipeline_produces_a_cited_answer():
    chunk = _chunk("c_1", 57, "3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    bundle = _FakeBundle(_FakeDenseIndex([("c_1", 0.9)]), _FakeMetadataStore({"c_1": chunk}))
    generator_client = _FakeGeneratorClient({
        "reasoning": "...", "answerable": True,
        "answers": [{"metric": "net_sales", "answer_value": "$32,765 million", "answer_unit": "millions"}],
        "answer_text": "3M's net sales were $32,765 million.",
        "used_chunk_ids": ["c_1"],
    })

    result = answer_question("What were 3M's net sales in FY2018?", bundle, FakeEmbedder(dimension=8), generator_client, _config(), generator_model="m")

    assert result.abstained is False
    assert result.answerable is True
    assert result.answers[0].value == "$32,765 million"
    assert len(result.citations) == 1
    assert result.citations[0]["page"] == 57
    assert result.retrieved_chunk_count == 1


def test_pipeline_abstains_when_no_chunks_are_retrieved():
    bundle = _FakeBundle(_FakeDenseIndex([]), _FakeMetadataStore({}))
    generator_client = _FakeGeneratorClient({"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_chunk_ids": []})

    result = answer_question("What were 3M's net sales in FY2099?", bundle, FakeEmbedder(dimension=8), generator_client, _config(), generator_model="m")

    assert result.abstained is True
    assert result.citations == []
    assert generator_client.calls == 0  # no chunks -> naive generator never called


def test_no_rerank_top_ids_taken_directly_from_dense_score_order():
    """No rerank step in C0 - rerank_top_k just truncates the dense ranking."""
    chunks = {
        "c_1": _chunk("c_1", 10, "first"),
        "c_2": _chunk("c_2", 20, "second"),
        "c_3": _chunk("c_3", 30, "third"),
    }
    bundle = _FakeBundle(_FakeDenseIndex([("c_1", 0.9), ("c_2", 0.8), ("c_3", 0.7)]), _FakeMetadataStore(chunks))
    generator_client = _FakeGeneratorClient({"reasoning": "...", "answerable": False, "answers": [], "answer_text": "n/a", "used_chunk_ids": []})

    config = _config()
    config = config.model_copy(update={"retrieval": config.retrieval.model_copy(update={"rerank_top_k": 2})})

    result = answer_question("q", bundle, FakeEmbedder(dimension=8), generator_client, config, generator_model="m")

    assert result.retrieved_chunk_count == 2  # only the top-2 by dense score, no rerank
