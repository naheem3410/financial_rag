"""C4 end-to-end pipeline wiring tests (NPCE + CoVe).
Combines test_pipeline_c3.py's fakes with test_pipeline_c2.py's CoVe fakes to check the full wiring.
"""
from __future__ import annotations

from config.schema import ExperimentConfig
from contracts.models import (
    CanonicalIdentifiers, Confidence, NumericRecord, Period, PlaceholderRegistryEntry, Proposition, PropositionMetadata,
)
from doubles import FakeEmbedder
from offline.indexing.dense_index_faiss import PersistentDenseIndex
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


def _make_numeric_proposition(proposition_id, source_uri, page, text, company, value, basis="reported", fiscal_year=2022):
    """Like _make_proposition, but with a real NumericRecord -- needed for
    the near-identical-versions check, which reads `.numeric`."""
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        numeric=NumericRecord(value=value, scale="millions", currency="USD", unit="absolute", period=Period(fiscal_year=fiscal_year, basis=basis)),
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


class _CompanyAwareFakeMetadataStore:
    """Unlike _FakeMetadataStore, this one actually filters by company --
    needed to prove the retry road (step 4.5) really finds something on
    the second try, not just returns the same fixed list regardless of
    which name was queried."""

    def __init__(self, propositions):
        self._propositions = list(propositions)

    def query(self, company=None, fiscal_year=None, quarter=None, filing_type=None, metrics=None):
        if company is None:
            return list(self._propositions)
        return [p for p in self._propositions if p.metadata.company == company]


class _FakeBundle:
    """Step 1.2: real code reads candidate vectors from `bundle.dense_index`
    instead of re-embedding, so the fake needs one too, pre-filled with the
    same fake embedder's output (deterministic, matches by text)."""

    def __init__(self, metadata_store, propositions=()):
        self.metadata_store = metadata_store
        self.dense_index = PersistentDenseIndex(dimension=8)
        embedder = FakeEmbedder(dimension=8)
        for prop in propositions:
            self.dense_index.upsert(prop.proposition_id, embedder.encode_passages([prop.text])[0])


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
                    for name in ("proposition_segmenter", "numeric_qualifier_resolver", "query_splitter", "fff_rewriter", "generator", "cove_verification", "step_planner")
                },
                "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
            },
            "corpus": {"pdf_dir": "data/pdfs", "required_docs_file": "data/metric_axis_required_docs.txt"},
        }
    )


def test_full_pipeline_two_sub_queries_both_cove_verified():
    prop_ni = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M Company had net income of $5,777 million for the year ended December 31, 2022.", company="3M Company")
    prop_rev = _make_proposition("p_rev", "3M_2022_10K", page=49, text="3M Company had total net sales of $34,229 million for the year ended December 31, 2022.", company="3M Company")
    bundle = _FakeBundle(_FakeMetadataStore([prop_ni, prop_rev]), [prop_ni, prop_rev])

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
    bundle = _FakeBundle(_FakeMetadataStore([]), [])
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


def test_a_plain_question_is_routed_straight():
    bundle = _FakeBundle(_FakeMetadataStore([]), [])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []}])

    result = answer_question(
        "What was 3M's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.routing_road == "straight"
    assert result.routing_capped is False


def test_a_split_question_is_routed_as_split_money():
    bundle = _FakeBundle(_FakeMetadataStore([]), [])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income", "total_revenue"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}, {"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
    ])

    result = answer_question(
        "What were 3M's net income and total revenue for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.routing_road == "split_money"
    assert "and" in result.routing_reason


def test_more_than_six_pieces_is_capped_at_six_for_real():
    """Decision 11's guard rail, exercised through the real fan-out: 7
    metrics come back from the splitter, but only 6 sub-queries actually
    run -- the 7th never reaches the fff/generator clients at all."""
    bundle = _FakeBundle(_FakeMetadataStore([]), [])
    seven_metrics = ["net_income", "total_revenue", "operating_margin", "gross_margin", "basic_eps", "diluted_eps", "gaap"]
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": seven_metrics, "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []} for _ in range(6)])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []} for _ in range(6)
    ])

    result = answer_question(
        "What were 3M's net income, total revenue, operating margin, gross margin, basic eps, diluted eps and gaap for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    # With an empty store, rewrite_to_ffts/generate_answer both skip the LLM
    # call on their own (their empty-candidates guard) for every piece --
    # so the real, meaningful proof capping happened is the count itself,
    # not the client call counts (which would be 0 either way here).
    assert result.sub_query_count == 6
    assert result.routing_capped is True


def test_a_typo_in_a_company_name_is_fixed_before_splitting():
    """Step 4.4: "Adobee" is close enough to the registry's real "Adobe" to
    be fixed automatically, and the fixed text -- not the typo -- is what
    reaches the splitter."""
    registry_with_adobe = list(REGISTRY) + [
        PlaceholderRegistryEntry(surface_form="Adobe", placeholder="<ENT_2>", canonical=CanonicalIdentifiers(ticker="ADBE")),
    ]
    bundle = _FakeBundle(_FakeMetadataStore([]), [])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["Adobe"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []}])

    result = answer_question(
        "What was Adobee's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config(), registry=registry_with_adobe,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.question_fix_outcome == "fixed"
    assert result.question_fixes == (("Adobee's", "Adobe's"),)
    assert splitter_client.calls[0]["query"] == "What was Adobe's net income for FY2022?"


def test_a_pronoun_with_no_named_company_asks_instead_of_guessing_and_calls_no_model():
    """Step 4.4's "What was it in 2019?" case, exercised through the real
    fan-out: none of the six call sites should ever be reached -- there is
    genuinely nothing yet to route, split, retrieve or generate against."""
    bundle = _FakeBundle(_FakeMetadataStore([]), [])
    splitter_client = _SequencedClient([])
    fff_client = _SequencedClient([])
    generator_client = _SequencedClient([])

    result = answer_question(
        "What was its net income in 2019?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.question_fix_outcome == "needs_clarification"
    assert result.abstained is True
    assert result.abstention_message == "Which company is this question about?"
    assert result.sub_query_count == 0
    assert splitter_client.calls == []
    assert fff_client.calls == []
    assert generator_client.calls == []


def test_the_retry_road_finds_it_under_the_other_known_name():
    """Step 4.5, exercised through the real fan-out: the canonical name the
    remapper resolves to ("3M Company", the registry's longest alias) isn't
    what's actually stored ("3M") -- the retry, using the registry's other
    known name, finds it and the sub-query is genuinely answered, not left
    to fail silently."""
    prop_ni = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M had net income of $5,777 million for the year ended December 31, 2022.", company="3M")
    bundle = _FakeBundle(_CompanyAwareFakeMetadataStore([prop_ni]), [prop_ni])

    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_response = {"reasoning": "...", "facts": [
        {"proposition_id": "p_ni", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,777 million"},
    ]}
    real_ffts = rewrite_to_ffts("q", [prop_ni], _SequencedClient([fff_response]), subject_override="<ENT_1>")
    fff_client = _SequencedClient([fff_response])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts[0].fff_id]},
    ])
    claim_extraction_client = _SequencedClient([
        {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]},
    ])
    independent_verification_client = _SequencedClient([
        {"reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts[0].fff_id]},
    ])
    prose_comparison_client = _SequencedClient([])

    result = answer_question(
        "What was 3M's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.retry_attempted is True
    assert result.retry_names_tried == ("3M",)
    assert result.abstained is False
    assert {a.value for a in result.answers} == {"$5,777 million"}


def test_a_retry_that_also_finds_nothing_is_an_honest_not_found_that_sub_query():
    """Neither the resolved name nor its only known sibling matches
    anything stored -- one retry, then that sub-query goes unanswered like
    any other missing-evidence case, never a crash and never a third try."""
    bundle = _FakeBundle(_CompanyAwareFakeMetadataStore([]), [])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([])
    generator_client = _SequencedClient([])

    result = answer_question(
        "What was 3M's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.retry_attempted is True
    assert result.retry_names_tried == ("3M",)
    assert result.abstained is True


# --- decision 11's "several versions" case (built 2026-09-27) ---


def test_a_reported_and_restated_value_are_both_answered_and_labelled_with_cove_on_both():
    """Step 4.1's own test row, exercised through the full C4 fan-out
    including CoVe verification of both labelled versions."""
    prop_reported = _make_numeric_proposition(
        "p_reported", "3M_2022_10K", 47, "3M Company had net income of $5,777 million for the year ended December 31, 2022.",
        company="3M Company", value=5777.0, basis="reported",
    )
    prop_restated = _make_numeric_proposition(
        "p_restated", "3M_2022_10K_A", 12, "3M Company restated net income to $5,820 million for the year ended December 31, 2022.",
        company="3M Company", value=5820.0, basis="restated",
    )
    bundle = _FakeBundle(_FakeMetadataStore([prop_reported, prop_restated]), [prop_reported, prop_restated])

    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_response_reported = {"reasoning": "...", "facts": [
        {"proposition_id": "p_reported", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,777 million"},
    ]}
    fff_response_restated = {"reasoning": "...", "facts": [
        {"proposition_id": "p_restated", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,820 million"},
    ]}
    real_ffts_reported = rewrite_to_ffts("q", [prop_reported], _SequencedClient([fff_response_reported]), subject_override="<ENT_1>")
    real_ffts_restated = rewrite_to_ffts("q", [prop_restated], _SequencedClient([fff_response_restated]), subject_override="<ENT_1>")

    fff_client = _SequencedClient([fff_response_reported, fff_response_restated])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts_reported[0].fff_id]},
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "net_income", "answer_value": "$5,820 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s net income was $5,820 million.", "used_fff_ids": [real_ffts_restated[0].fff_id]},
    ])
    claim_extraction_client = _SequencedClient([
        {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (originally reported, FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]},
        {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (restated, FY2022): $5,820 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5820", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]},
    ])
    independent_verification_client = _SequencedClient([
        {"reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_reported[0].fff_id]},
        {"reasoning": "...", "supported": True, "answer_value": "5820", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_restated[0].fff_id]},
    ])
    prose_comparison_client = _SequencedClient([])

    result = answer_question(
        "What was 3M's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.near_identical_detected is True
    assert result.near_identical_detail == "basis"
    assert result.abstained is False
    labels = {a.metric for a in result.answers}
    assert labels == {"net_income (originally reported)", "net_income (restated)"}


# --- step 4.6: step by step ---


def test_a_two_step_question_runs_the_steps_in_order_with_cove_on_both():
    """The plan's own "piece two needs piece one" case, exercised through
    the full C4 fan-out including CoVe verification on each step -- the
    live API's own path, not a shortcut."""
    prop_rev = _make_proposition("p_rev", "3M_2022_10K", page=49, text="3M had total net sales of $34,229 million for the year ended December 31, 2022.", company="3M")
    prop_ni = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M had net income of $5,777 million for the year ended December 31, 2022.", company="3M")
    bundle = _FakeBundle(_FakeMetadataStore([prop_rev, prop_ni]), [prop_rev, prop_ni])

    planner_client = _SequencedClient([{
        "reasoning": "...",
        "steps": [
            {"step_id": 1, "question": "Which company had the highest revenue in 2022?"},
            {"step_id": 2, "question": "Given that {step_1}, what was its net income in 2022?"},
        ],
    }])
    splitter_client = _SequencedClient([
        {"reasoning": "...", "entities": ["3M"], "metrics": ["total_revenue"], "periods": [{"fiscal_year": 2022}]},
        {"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]},
    ])

    fff_response_rev = {"reasoning": "...", "facts": [
        {"proposition_id": "p_rev", "subject": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022", "value_text": "$34,229 million"},
    ]}
    fff_response_ni = {"reasoning": "...", "facts": [
        {"proposition_id": "p_ni", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,777 million"},
    ]}
    real_ffts_rev = rewrite_to_ffts("q", [prop_rev], _SequencedClient([fff_response_rev]), subject_override="<ENT_1>")
    real_ffts_ni = rewrite_to_ffts("q", [prop_ni], _SequencedClient([fff_response_ni]), subject_override="<ENT_1>")

    fff_client = _SequencedClient([fff_response_rev, fff_response_ni])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "total_revenue", "answer_value": "$34,229 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s total revenue was $34,229 million.", "used_fff_ids": [real_ffts_rev[0].fff_id]},
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts_ni[0].fff_id]},
    ])
    claim_extraction_client = _SequencedClient([
        {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> total revenue (FY2022): $34,229 million",
             "entity": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022",
             "asserted_value": "34229", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]},
        {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]},
    ])
    independent_verification_client = _SequencedClient([
        {"reasoning": "...", "supported": True, "answer_value": "34229", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_rev[0].fff_id]},
        {"reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_ni[0].fff_id]},
    ])
    prose_comparison_client = _SequencedClient([])

    result = answer_question(
        "What was the net income of the company with the highest revenue in 2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
        step_planner_client=planner_client,
    )

    assert result.routing_road == "step_by_step"
    assert result.abstained is False
    assert len(result.step_plan) == 2
    assert "3M" in result.answer_text
    assert "$5,777 million" in result.answer_text


def test_a_planner_loop_is_rejected_and_falls_back_to_the_normal_road():
    """A plan the model returns that references its own step is untrustworthy
    as a whole -- rejected outright -- so the question is still answered,
    just the ordinary way instead of step by step."""
    bundle = _FakeBundle(_FakeMetadataStore([]), [])  # kept empty -- only the fallback path matters here

    planner_client = _SequencedClient([{
        "reasoning": "...",
        "steps": [{"step_id": 1, "question": "What was {step_1}'s net income in 2022?"}],
    }])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([])
    generator_client = _SequencedClient([])

    result = answer_question(
        "What was the net income of the company with the highest revenue in 2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
        step_planner_client=planner_client,
    )

    assert result.routing_road == "step_by_step"  # the router's own classification still stands
    assert result.step_plan == ()  # but no steps actually ran -- the plan was rejected
    assert result.abstained is True  # empty store, the fallback path's own honest "not found"


def test_no_step_planner_client_falls_back_to_the_normal_road_unchanged():
    """Omitting step_planner_client costs nothing -- the same safe default
    every other optional call site in this project falls back to."""
    bundle = _FakeBundle(_FakeMetadataStore([]), [])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([])
    generator_client = _SequencedClient([])

    result = answer_question(
        "What was the net income of the company with the highest revenue in 2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
        # step_planner_client omitted entirely
    )

    assert result.routing_road == "step_by_step"
    assert result.step_plan == ()
    assert result.abstained is True


def test_dictionary_resolves_an_unmapped_metric_phrase_end_to_end():
    """Step 2.2: the model can't map "net sales" to a lexicon entry, but the
    free-text dictionary knows it means total_revenue -- the sub-query
    still gets answered, not silently dropped."""
    prop_rev = _make_proposition("p_rev", "3M_2022_10K", page=49, text="3M Company had total net sales of $34,229 million for the year ended December 31, 2022.", company="3M Company")
    bundle = _FakeBundle(_FakeMetadataStore([prop_rev]), [prop_rev])

    splitter_client = _SequencedClient([
        {"reasoning": "...", "entities": ["3M"], "metrics": [], "unmapped_metrics": ["net sales"], "periods": [{"fiscal_year": 2022}]}
    ])
    fff_response = {"reasoning": "...", "facts": [
        {"proposition_id": "p_rev", "subject": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022", "value_text": "$34,229 million"},
    ]}
    real_ffts = rewrite_to_ffts("q", [prop_rev], _SequencedClient([fff_response]), subject_override="<ENT_1>")
    fff_client = _SequencedClient([fff_response])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "total_revenue", "answer_value": "$34,229 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s total revenue was $34,229 million.", "used_fff_ids": [real_ffts[0].fff_id]},
    ])
    claim_extraction_client = _SequencedClient([
        {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> total revenue (FY2022): $34,229 million",
             "entity": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022",
             "asserted_value": "34229", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]},
    ])
    independent_verification_client = _SequencedClient([
        {"reasoning": "...", "supported": True, "answer_value": "34229", "answer_unit": "USD",
         "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts[0].fff_id]},
    ])
    prose_comparison_client = _SequencedClient([])
    logged = []

    result = answer_question(
        "What were 3M's net sales for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
        dictionary_lookup=lambda phrase: {"net sales": "total_revenue"}.get(phrase),
        unmatched_logger=logged.append,
    )

    assert result.sub_query_count == 1
    assert result.abstained is False
    assert "34,229" in result.answer_text
    assert logged == []  # resolved, so never logged as unmatched


def test_missing_vectors_abstain_that_sub_query_instead_of_crashing():
    """Step 1.2: if the dense index is missing a candidate's vector (more
    than half of them), that sub-query is reported unanswerable -- the
    question does not crash, and no LLM calls are wasted on it."""
    prop_ni = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M Company had net income of $5,777 million for the year ended December 31, 2022.", company="3M Company")
    bundle = _FakeBundle(_FakeMetadataStore([prop_ni]), [])  # in the database, but never indexed as a vector

    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([])
    generator_client = _SequencedClient([])
    claim_extraction_client = _SequencedClient([])
    independent_verification_client = _SequencedClient([])
    prose_comparison_client = _SequencedClient([])

    result = answer_question(
        "What was 3M's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.abstained is True
    assert result.answerable is False
    assert len(fff_client.calls) == 0  # never reached the FFF rewrite step
    assert len(generator_client.calls) == 0  # never reached the generator either


def test_one_conflicting_claim_forces_whole_answer_abstention():
    """One conflicting claim discards the whole multi-claim answer even
    when the other claim genuinely matches -- the pre-registered C4 prediction."""
    prop_ni = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M Company had net income of $5,777 million for the year ended December 31, 2022.", company="3M Company")
    prop_rev = _make_proposition("p_rev", "3M_2022_10K", page=49, text="3M Company had total net sales of $34,229 million for the year ended December 31, 2022.", company="3M Company")
    bundle = _FakeBundle(_FakeMetadataStore([prop_ni, prop_rev]), [prop_ni, prop_rev])

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
    # claim 1 genuinely matches, claim 2 spuriously conflicts despite real
    # supporting evidence -- mirrors the instability seen in real C2 data
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
