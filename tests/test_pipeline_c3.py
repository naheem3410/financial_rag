"""C3 end-to-end pipeline wiring tests (NPCE-on).
Uses a fake metadata store, a fake pass-through reranker, and fake Query
Splitter/FFF/Generator LLM clients (no real Postgres/FAISS/models/API
calls) — these test that the full split -> remap -> filter -> retrieve ->
rerank -> FFF-rewrite -> pair -> generate -> assemble -> reverse-map ->
cite -> abstain wiring is correct, not the already-tested pieces it
composes.
"""
from __future__ import annotations

from config.schema import ExperimentConfig
from contracts.models import CanonicalIdentifiers, Confidence, PlaceholderRegistryEntry, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from online.curation.fff_rewriter import rewrite_to_ffts
from online.pipeline_c3 import answer_question


def _make_proposition(proposition_id: str, source_uri: str, page: int, text: str, company: str) -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company=company, filing_type="10-K", filing_date="2022-12-31",
            section="", page=page, source_uri=source_uri,
        ),
    )


class _FakeMetadataStore:
    """A wiring-test double: returns every proposition it holds regardless
    of filter args -- real company/period filtering is already covered by
    tests/test_metadata_filter.py; this only needs to prove the pipeline
    calls it and uses what comes back."""

    def __init__(self, propositions):
        self._propositions = list(propositions)

    def query(self, company=None, fiscal_year=None, quarter=None, filing_type=None):
        return list(self._propositions)


class _FakeBundle:
    def __init__(self, metadata_store):
        self.metadata_store = metadata_store


class _FakeReranker:
    def rerank(self, sq, props, k):
        return list(props[:k])


class _SequencedClient:
    """Pops one pre-set response per `complete()` call, in order -- needed
    because C3 calls the FFF/Generator client once per sub-query with
    different expected content each time."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


REGISTRY = [
    PlaceholderRegistryEntry(surface_form="3M", placeholder="<ENT_1>", canonical=CanonicalIdentifiers(ticker="MMM")),
    PlaceholderRegistryEntry(surface_form="3M Company", placeholder="<ENT_1>", canonical=CanonicalIdentifiers(ticker="MMM")),
]


def _config():
    return ExperimentConfig.model_validate(
        {
            "experiment_id": "c3_npce",
            "enable_npce": True,
            "enable_cove": False,
            "retrieval": {"mode": "hybrid", "top_k": 20, "rerank_top_k": 10, "enable_reranker": True, "time_weighting": False},
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


def _splitter_response():
    return {
        "reasoning": "...", "entities": ["3M"], "metrics": ["net_income", "total_revenue"], "periods": [{"fiscal_year": 2022}],
    }


def test_two_sub_queries_both_answered_no_partial_drop():
    """The core NPCE demonstration: two metrics, each with its own
    metadata-filtered retrieval budget, both answered -- unlike C1's shared
    single-window capacity confound."""
    prop_ni = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M Company had net income of $5,777 million for the year ended December 31, 2022.", company="3M Company")
    prop_rev = _make_proposition("p_rev", "3M_2022_10K", page=49, text="3M Company had total net sales of $34,229 million for the year ended December 31, 2022.", company="3M Company")
    bundle = _FakeBundle(_FakeMetadataStore([prop_ni, prop_rev]))

    splitter_client = _SequencedClient([_splitter_response()])

    fff_response_ni = {"reasoning": "...", "facts": [
        {"proposition_id": "p_ni", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,777 million"},
    ]}
    fff_response_rev = {"reasoning": "...", "facts": [
        {"proposition_id": "p_rev", "subject": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022", "value_text": "$34,229 million"},
    ]}
    # fff_id is computed deterministically inside rewrite_to_ffts -- derive the
    # real ids the same way test_pipeline_c1.py does, so the generator's
    # used_fff_ids reference something that actually exists.
    real_ffts_ni = rewrite_to_ffts("q", [prop_ni], _SequencedClient([fff_response_ni]), subject_override="<ENT_1>")
    real_ffts_rev = rewrite_to_ffts("q", [prop_rev], _SequencedClient([fff_response_rev]), subject_override="<ENT_1>")

    fff_client = _SequencedClient([fff_response_ni, fff_response_rev])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts_ni[0].fff_id]},
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "total_revenue", "answer_value": "$34,229 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s total revenue was $34,229 million.", "used_fff_ids": [real_ffts_rev[0].fff_id]},
    ])

    result = answer_question(
        "What were 3M's net income and total revenue for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client, _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m",
    )

    assert result.sub_query_count == 2
    assert result.abstained is False
    assert len(result.answers) == 2
    assert {a.value for a in result.answers} == {"$5,777 million", "$34,229 million"}
    # Real company name restored in the final text, never left as a placeholder.
    assert "3M" in result.answer_text
    assert "<ENT_1>" not in result.answer_text
    assert len(result.citations) == 2


def test_abstains_when_no_sub_query_is_answerable():
    bundle = _FakeBundle(_FakeMetadataStore([]))
    splitter_client = _SequencedClient([_splitter_response()])
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}, {"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
    ])

    result = answer_question(
        "What were 3M's net income and total revenue for FY2099?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client, _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m",
    )

    assert result.abstained is True
    assert result.citations == []


def test_placeholder_map_never_leaks_into_generator_prompt():
    """The real company name must never reach the Generator's payload --
    only the placeholder token (subject_override + rendered question)."""
    prop = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M Company had net income of $5,777 million for the year ended December 31, 2022.", company="3M Company")
    bundle = _FakeBundle(_FakeMetadataStore([prop]))
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_response = {"reasoning": "...", "facts": [
        {"proposition_id": "p_ni", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,777 million"},
    ]}
    real_ffts = rewrite_to_ffts("q", [prop], _SequencedClient([fff_response]), subject_override="<ENT_1>")
    fff_client = _SequencedClient([fff_response])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts[0].fff_id]},
    ])

    answer_question(
        "What was 3M's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client, _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m",
    )

    generator_payload = generator_client.calls[0]
    assert "3M" not in generator_payload["question"]
    assert "<ENT_1>" in generator_payload["question"]
