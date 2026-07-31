"""C4 end-to-end pipeline wiring tests (NPCE + CoVe).
Combines tests/test_pipeline_c3.py's fakes (metadata store with .query(),
splitter client) with tests/test_pipeline_c2.py's CoVe fakes -- these test
that the full split -> remap -> filter -> retrieve -> rerank -> FFF-rewrite
-> pair -> generate -> assemble -> CoVe-verify -> reverse-map -> cite ->
abstain wiring is correct, not the already-tested pieces it composes.
"""
from __future__ import annotations

from config.schema import ExperimentConfig
from contracts.models import CanonicalIdentifiers, Confidence, PlaceholderRegistryEntry, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from online.curation.fff_rewriter import rewrite_to_ffts
from online.pipeline_c4 import answer_question


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
            "experiment_id": "c4_full",
            "enable_npce": True,
            "enable_cove": True,
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


def test_full_pipeline_two_sub_queries_both_cove_verified():
    prop_ni = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M Company had net income of $5,777 million for the year ended December 31, 2022.", company="3M Company")
    prop_rev = _make_proposition("p_rev", "3M_2022_10K", page=49, text="3M Company had total net sales of $34,229 million for the year ended December 31, 2022.", company="3M Company")
    bundle = _FakeBundle(_FakeMetadataStore([prop_ni, prop_rev]))

    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income", "total_revenue"], "periods": [{"fiscal_year": 2022}]}]
    )

    fff_response_ni = {"reasoning": "...", "facts": [
        {"proposition_id": "p_ni", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,777 million"},
    ]}
    fff_response_rev = {"reasoning": "...", "facts": [
        {"proposition_id": "p_rev", "subject": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022", "value_text": "$34,229 million"},
    ]}
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

    claim_extraction_client = _SequencedClient([
        {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
            {"claim_id": "c_2", "claim_text": "<ENT_1> total revenue (FY2022): $34,229 million",
             "entity": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022",
             "asserted_value": "34229", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]},
    ])
    independent_verification_client = _SequencedClient([
        {"reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_ni[0].fff_id]},
        {"reasoning": "...", "supported": True, "answer_value": "34229", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_rev[0].fff_id]},
    ])
    prose_comparison_client = _SequencedClient([])  # both claims numeric -> deterministic comparator, never LLM

    result = answer_question(
        "What were 3M's net income and total revenue for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.sub_query_count == 2
    assert result.abstained is False
    assert len(result.verification_trace) == 2
    assert all(c["verdict"] == "match" for c in result.verification_trace)
    assert "3M" in result.answer_text
    assert "<ENT_1>" not in result.answer_text
    assert len(result.citations) == 2


def test_abstains_when_no_sub_query_is_answerable_cove_never_invoked():
    bundle = _FakeBundle(_FakeMetadataStore([]))
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income", "total_revenue"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}, {"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
    ])
    claim_extraction_client = _SequencedClient([])
    independent_verification_client = _SequencedClient([])
    prose_comparison_client = _SequencedClient([])

    result = answer_question(
        "What were 3M's net income and total revenue for FY2099?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.abstained is True
    assert result.citations == []
    assert len(claim_extraction_client.calls) == 0  # nothing to verify -- CoVe never invoked


def test_one_conflicting_claim_forces_whole_answer_abstention():
    """The mechanism under investigation: even when one sub-query's claim
    genuinely matches, a single conflict verdict on another claim (any
    non-match claim is material) discards the entire multi-claim answer
    -- this is what the pre-registered C4 prediction is about."""
    prop_ni = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M Company had net income of $5,777 million for the year ended December 31, 2022.", company="3M Company")
    prop_rev = _make_proposition("p_rev", "3M_2022_10K", page=49, text="3M Company had total net sales of $34,229 million for the year ended December 31, 2022.", company="3M Company")
    bundle = _FakeBundle(_FakeMetadataStore([prop_ni, prop_rev]))

    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income", "total_revenue"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_response_ni = {"reasoning": "...", "facts": [
        {"proposition_id": "p_ni", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,777 million"},
    ]}
    fff_response_rev = {"reasoning": "...", "facts": [
        {"proposition_id": "p_rev", "subject": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022", "value_text": "$34,229 million"},
    ]}
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

    claim_extraction_client = _SequencedClient([
        {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
            {"claim_id": "c_2", "claim_text": "<ENT_1> total revenue (FY2022): $34,229 million",
             "entity": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022",
             "asserted_value": "34229", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]},
    ])
    # Claim 1 (net income) genuinely matches; claim 2 spuriously conflicts
    # despite real supporting evidence -- the exact instability pattern
    # found in the real C2 data.
    independent_verification_client = _SequencedClient([
        {"reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_ni[0].fff_id]},
        {"reasoning": "...", "supported": True, "answer_value": "99999", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_rev[0].fff_id]},
    ])
    prose_comparison_client = _SequencedClient([])

    result = answer_question(
        "What were 3M's net income and total revenue for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    verdicts = {c["verdict"] for c in result.verification_trace}
    assert "match" in verdicts
    assert "conflict" in verdicts
    assert result.abstained is True  # one conflicting claim discards the whole answer
