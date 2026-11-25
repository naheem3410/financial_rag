"""C4 end-to-end pipeline wiring tests (NPCE + CoVe).
Combines test_pipeline_c3.py's fakes with test_pipeline_c2.py's CoVe fakes to check the full wiring.
"""
from __future__ import annotations

import threading
import time

from concurrent.futures import ThreadPoolExecutor

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


def _make_numeric_proposition(proposition_id, source_uri, page, text, company, value, basis="reported", fiscal_year=2022, metrics=()):
    """Like _make_proposition, but with a real NumericRecord -- needed for
    the near-identical-versions check, which reads `.numeric`. `metrics`
    is the canonical-metric-id signal the metric-wording collision check reads."""
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        numeric=NumericRecord(value=value, scale="millions", currency="USD", unit="absolute", period=Period(fiscal_year=fiscal_year, basis=basis)),
        confidence=Confidence(extraction_tier="rules"),
        metrics=metrics,
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
    def rerank(self, sq, props, k, ctx=None):
        return list(props[:k])


class _SequencedClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


class _FirstEvidenceAnswerClient:
    """Fake independent-verification client that always answers using
    whichever FFF is FIRST in the evidence list it was handed, keyed by
    `fff_id` -> canned response. If two sub-answers' evidence were ever
    pooled into one call, this would answer every claim with the first
    sub-answer's value, wrongly conflicting the second one's own real,
    correct claim. Scoped per sub-answer, each call only ever has one
    sub-answer's evidence, so this always answers correctly."""

    def __init__(self, answers_by_fff_id: dict):
        self._answers = answers_by_fff_id
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        first_fff_id = payload["evidence"][0]["fff_id"]
        return self._answers[first_fff_id]


class _ContentKeyedClient:
    """Like `_SequencedClient`, but matches a response to a call by
    inspecting the payload instead of by call order.

    Decision 38 parallelized the per-sub-query and per-sub-answer loops,
    so calls to a shared fake client can now arrive from several threads
    at once -- which call reaches this fake first is not guaranteed to
    match the sub-query/sub-answer's own index order, so an order-based
    fake (`_SequencedClient`) would risk handing the wrong canned response
    to the wrong call. `responses` is a list of (predicate, response)
    pairs, first match wins. Thread-safe: `.calls` is appended under a lock."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def complete(self, payload):
        with self._lock:
            self.calls.append(payload)
        for predicate, response in self._responses:
            if predicate(payload):
                return response
        raise AssertionError(f"_ContentKeyedClient: no predicate matched payload {payload!r}")


class _DelayedClient:
    """Wraps another fake client, sleeping `delay_seconds` before every
    `complete()` -- the artificial, measurable stand-in for real LLM
    latency, so a test can assert decision 38's parallel loops actually
    overlap that latency instead of queuing it."""

    def __init__(self, inner, delay_seconds: float):
        self._inner = inner
        self._delay_seconds = delay_seconds

    @property
    def calls(self):
        return self._inner.calls

    def complete(self, payload):
        time.sleep(self._delay_seconds)
        return self._inner.complete(payload)


class _SelectivelyDelayedClient:
    """Like `_DelayedClient`, but only sleeps for calls matching
    `slow_predicate` -- used to force one specific sub-query's work to
    finish well after the others', so a test can check the final result's
    ordering survives a deliberately reversed completion order."""

    def __init__(self, inner, slow_predicate, delay_seconds: float):
        self._inner = inner
        self._slow_predicate = slow_predicate
        self._delay_seconds = delay_seconds

    @property
    def calls(self):
        return self._inner.calls

    def complete(self, payload):
        if self._slow_predicate(payload):
            time.sleep(self._delay_seconds)
        return self._inner.complete(payload)


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

    # Decision 38: the two sub-queries' fff/generator/claim-extraction/
    # independent-verification calls now run on separate threads, so which
    # one reaches a shared fake first is not guaranteed -- these fakes
    # match by payload content instead of call order (see
    # `_ContentKeyedClient`'s own docstring).
    # Keyed by the rendered sub-query question text, not by which
    # candidate propositions happen to be in the payload -- the test's fake
    # metadata store returns both propositions for every company, so the
    # candidate list alone can't tell the two sub-queries apart; the
    # question text (built from each sub-query's own metric) can.
    fff_client = _ContentKeyedClient([
        (lambda p: "net income" in p["sub_query"]["question"], fff_response_ni),
        (lambda p: "total revenue" in p["sub_query"]["question"], fff_response_rev),
    ])
    generator_client = _ContentKeyedClient([
        (lambda p: "net income" in p["question"], {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
            "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts_ni[0].fff_id],
        }),
        (lambda p: "total revenue" in p["question"], {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "total_revenue", "answer_value": "$34,229 million", "answer_unit": "millions"}],
            "answer_text": "<ENT_1>'s total revenue was $34,229 million.", "used_fff_ids": [real_ffts_rev[0].fff_id],
        }),
    ])

    # Each sub-answer gets its own verify() call, so claim extraction
    # happens once per sub-answer here, not once for the whole draft.
    claim_extraction_client = _ContentKeyedClient([
        (lambda p: "net income" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
        (lambda p: "total revenue" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> total revenue (FY2022): $34,229 million",
             "entity": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022",
             "asserted_value": "34229", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
    ])
    independent_verification_client = _ContentKeyedClient([
        (lambda p: "net_income" in p["verification_question"], {
            "reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_ni[0].fff_id],
        }),
        (lambda p: "total_revenue" in p["verification_question"], {
            "reasoning": "...", "supported": True, "answer_value": "34229", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_rev[0].fff_id],
        }),
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
    # Each call saw only its own sub-answer's evidence, never the other's
    # -- checked by content, since thread completion order isn't fixed.
    assert len(independent_verification_client.calls) == 2
    fff_ids_seen_per_call = [{f["fff_id"] for f in call["evidence"]} for call in independent_verification_client.calls]
    assert {real_ffts_ni[0].fff_id} in fff_ids_seen_per_call
    assert {real_ffts_rev[0].fff_id} in fff_ids_seen_per_call


def test_one_bad_unrelated_sub_answer_never_drags_down_an_independently_correct_one():
    """Two unrelated sub-answers (net income, total revenue), one with a
    problem, one perfectly correct -- proves the correct one is never
    dragged down by the other's problem. `_FirstEvidenceAnswerClient`
    always answers using whichever FFF comes first in its evidence; if the
    two sub-answers' evidence were ever pooled into one call, both claims
    would be checked against net income's evidence and revenue would
    wrongly come back `conflict`. Scoped per sub-answer, each call only
    has its own evidence, so revenue verifies `match` on its own."""
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

    # Decision 38: these two sub-queries' calls now run on separate
    # threads, so match by payload content, not call order (see
    # `_ContentKeyedClient`'s own docstring).
    # Keyed by the rendered sub-query question text, not by which
    # candidate propositions happen to be in the payload -- the test's fake
    # metadata store returns both propositions for every company, so the
    # candidate list alone can't tell the two sub-queries apart; the
    # question text (built from each sub-query's own metric) can.
    fff_client = _ContentKeyedClient([
        (lambda p: "net income" in p["sub_query"]["question"], fff_response_ni),
        (lambda p: "total revenue" in p["sub_query"]["question"], fff_response_rev),
    ])
    generator_client = _ContentKeyedClient([
        (lambda p: "net income" in p["question"], {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
            "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts_ni[0].fff_id],
        }),
        (lambda p: "total revenue" in p["question"], {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "total_revenue", "answer_value": "$34,229 million", "answer_unit": "millions"}],
            "answer_text": "<ENT_1>'s total revenue was $34,229 million.", "used_fff_ids": [real_ffts_rev[0].fff_id],
        }),
    ])
    claim_extraction_client = _ContentKeyedClient([
        (lambda p: "net income" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
        (lambda p: "total revenue" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> total revenue (FY2022): $34,229 million",
             "entity": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022",
             "asserted_value": "34229", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
    ])
    # If revenue's call ever saw net income's evidence first, it would
    # wrongly answer with net income's value instead of its own.
    independent_verification_client = _FirstEvidenceAnswerClient({
        real_ffts_ni[0].fff_id: {
            "reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_ni[0].fff_id],
        },
        real_ffts_rev[0].fff_id: {
            "reasoning": "...", "supported": True, "answer_value": "34229", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_rev[0].fff_id],
        },
    })
    prose_comparison_client = _SequencedClient([])

    result = answer_question(
        "What were 3M's net income and total revenue for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    net_income_claim = next(c for c in result.verification_trace if "net income" in c["claim_text"])
    revenue_claim = next(c for c in result.verification_trace if "total revenue" in c["claim_text"])
    assert net_income_claim["verdict"] == "match"
    assert revenue_claim["verdict"] == "match"
    assert result.abstained is False
    assert result.confidence_score == 1.0


def test_per_sub_answer_verification_keeps_the_independence_invariant():
    """Extends the `test_cove_independence`-style check to this call site:
    now that `verify()` is called once PER sub-answer instead of once
    pooled, (a) `execute_independent_verification`'s own signature still
    takes no draft -- unchanged, enforced elsewhere -- and (b) no
    sub-answer's draft text/claim text ever shows up in a DIFFERENT
    sub-answer's claim-extraction or independent-verification payload.
    Each sub-answer's own claim text is expected in its own payloads; what
    must never happen is cross-contamination between them."""
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

    # Decision 38: these two sub-queries' calls now run on separate
    # threads, so match by payload content, not call order (see
    # `_ContentKeyedClient`'s own docstring).
    # Keyed by the rendered sub-query question text, not by which
    # candidate propositions happen to be in the payload -- the test's fake
    # metadata store returns both propositions for every company, so the
    # candidate list alone can't tell the two sub-queries apart; the
    # question text (built from each sub-query's own metric) can.
    fff_client = _ContentKeyedClient([
        (lambda p: "net income" in p["sub_query"]["question"], fff_response_ni),
        (lambda p: "total revenue" in p["sub_query"]["question"], fff_response_rev),
    ])
    generator_client = _ContentKeyedClient([
        (lambda p: "net income" in p["question"], {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
            "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts_ni[0].fff_id],
        }),
        (lambda p: "total revenue" in p["question"], {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "total_revenue", "answer_value": "$34,229 million", "answer_unit": "millions"}],
            "answer_text": "<ENT_1>'s total revenue was $34,229 million.", "used_fff_ids": [real_ffts_rev[0].fff_id],
        }),
    ])
    claim_extraction_client = _ContentKeyedClient([
        (lambda p: "net income" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
        (lambda p: "total revenue" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> total revenue (FY2022): $34,229 million",
             "entity": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022",
             "asserted_value": "34229", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
    ])
    independent_verification_client = _ContentKeyedClient([
        (lambda p: "net_income" in p["verification_question"], {
            "reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_ni[0].fff_id],
        }),
        (lambda p: "total_revenue" in p["verification_question"], {
            "reasoning": "...", "supported": True, "answer_value": "34229", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_rev[0].fff_id],
        }),
    ])
    prose_comparison_client = _SequencedClient([])

    answer_question(
        "What were 3M's net income and total revenue for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    # Found by content, not call position -- thread completion order isn't
    # guaranteed, so "which call came first" is no longer a meaningful question.
    assert len(claim_extraction_client.calls) == 2
    net_income_draft = next(c["draft"] for c in claim_extraction_client.calls if "net income" in c["draft"])
    revenue_draft = next(c["draft"] for c in claim_extraction_client.calls if "total revenue" in c["draft"])
    assert "net income" in net_income_draft and "total revenue" not in net_income_draft
    assert "total revenue" in revenue_draft and "net income" not in revenue_draft

    # No `draft` key ever reaches independent verification, and neither
    # sub-answer's own text crosses into the other's payload.
    assert len(independent_verification_client.calls) == 2
    for sent in independent_verification_client.calls:
        assert "draft" not in sent
    import json as _json
    net_income_call = next(c for c in independent_verification_client.calls if "net_income" in c["verification_question"])
    revenue_call = next(c for c in independent_verification_client.calls if "total_revenue" in c["verification_question"])
    assert net_income_draft not in _json.dumps(revenue_call, default=str)
    assert revenue_draft not in _json.dumps(net_income_call, default=str)


def test_near_identical_labelled_variants_each_verify_independently_against_own_evidence():
    """Two labelled variants of the same metric (like General Mills'
    "total" $1,692.1M vs. "customers" $1,720.4M receivables) are both real,
    evidenced, independently correct numbers. Pooling their evidence into
    one CoVe call lets the independent verifier pick one value as "the"
    truth and the other variant comes back a false `conflict`.
    `_FirstEvidenceAnswerClient` simulates exactly that "picks one value"
    behavior; scoped per sub-answer, each labelled variant's own call only
    has its own evidence, so both verify `match`."""
    prop_total = _make_numeric_proposition(
        "p_total", "3M_2022_10K", 47, "3M Company had total receivables of $1,692.1 million for the year ended December 31, 2022.",
        company="3M Company", value=1692.1, metrics=("accounts_receivable",),
    )
    prop_customers = _make_numeric_proposition(
        "p_customers", "3M_2022_10K_A", 48, "3M Company had customers receivables of $1,720.4 million for the year ended December 31, 2022.",
        company="3M Company", value=1720.4, metrics=("accounts_receivable",),
    )
    bundle = _FakeBundle(_FakeMetadataStore([prop_total, prop_customers]), [prop_total, prop_customers])

    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["accounts_receivable"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_response_total = {"reasoning": "...", "facts": [
        {"proposition_id": "p_total", "subject": "<ENT_1>", "metric": "accounts_receivable", "period_label": "FY2022", "value_text": "$1,692.1 million"},
    ]}
    fff_response_customers = {"reasoning": "...", "facts": [
        {"proposition_id": "p_customers", "subject": "<ENT_1>", "metric": "accounts_receivable", "period_label": "FY2022", "value_text": "$1,720.4 million"},
    ]}
    real_ffts_total = rewrite_to_ffts("q", [prop_total], _SequencedClient([fff_response_total]), subject_override="<ENT_1>")
    real_ffts_customers = rewrite_to_ffts("q", [prop_customers], _SequencedClient([fff_response_customers]), subject_override="<ENT_1>")

    fff_client = _SequencedClient([fff_response_total, fff_response_customers])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "accounts_receivable", "answer_value": "$1,692.1 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s accounts receivable (total) was $1,692.1 million.", "used_fff_ids": [real_ffts_total[0].fff_id]},
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "accounts_receivable", "answer_value": "$1,720.4 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s accounts receivable (customers) was $1,720.4 million.", "used_fff_ids": [real_ffts_customers[0].fff_id]},
    ])
    # Decision 38: the two labelled versions' own CoVe `verify()` calls now
    # run on separate threads (one per sub-answer), so claim extraction
    # matches by payload content, not call order.
    claim_extraction_client = _ContentKeyedClient([
        (lambda p: "total" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> accounts receivable (total, FY2022): $1,692.1 million",
             "entity": "<ENT_1>", "metric": "accounts_receivable", "period_label": "FY2022",
             "asserted_value": "1692.1", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
        (lambda p: "customers" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> accounts receivable (customers, FY2022): $1,720.4 million",
             "entity": "<ENT_1>", "metric": "accounts_receivable", "period_label": "FY2022",
             "asserted_value": "1720.4", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
    ])
    independent_verification_client = _FirstEvidenceAnswerClient({
        real_ffts_total[0].fff_id: {
            "reasoning": "...", "supported": True, "answer_value": "1692.1", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_total[0].fff_id],
        },
        real_ffts_customers[0].fff_id: {
            "reasoning": "...", "supported": True, "answer_value": "1720.4", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_customers[0].fff_id],
        },
    })
    prose_comparison_client = _SequencedClient([])

    result = answer_question(
        "What was 3M's accounts receivable for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.near_identical_detected is True
    assert result.near_identical_detail == "metric_wording"
    assert len(result.verification_trace) == 2
    assert all(c["verdict"] == "match" for c in result.verification_trace)
    assert result.abstained is False


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
    # Decision 38: the two labelled versions' own CoVe `verify()` calls now
    # run on separate threads (one per sub-answer), so match by payload
    # content, not call order.
    claim_extraction_client = _ContentKeyedClient([
        (lambda p: "reported" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (originally reported, FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
        (lambda p: "restated" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (restated, FY2022): $5,820 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5820", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
    ])
    # Keyed by evidence, not `verification_question` text -- both labelled
    # versions share the same (unsuffixed) entity/metric/period_label, so
    # the question text alone can't tell them apart; their FFF evidence can.
    independent_verification_client = _FirstEvidenceAnswerClient({
        real_ffts_reported[0].fff_id: {
            "reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_reported[0].fff_id],
        },
        real_ffts_restated[0].fff_id: {
            "reasoning": "...", "supported": True, "answer_value": "5820", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_restated[0].fff_id],
        },
    })
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

    # Decision 38: these two sub-queries' calls now run on separate
    # threads, so match by payload content, not call order.
    # Keyed by the rendered sub-query question text, not by which
    # candidate propositions happen to be in the payload -- the test's fake
    # metadata store returns both propositions for every company, so the
    # candidate list alone can't tell the two sub-queries apart; the
    # question text (built from each sub-query's own metric) can.
    fff_client = _ContentKeyedClient([
        (lambda p: "net income" in p["sub_query"]["question"], fff_response_ni),
        (lambda p: "total revenue" in p["sub_query"]["question"], fff_response_rev),
    ])
    generator_client = _ContentKeyedClient([
        (lambda p: "net income" in p["question"], {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
            "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts_ni[0].fff_id],
        }),
        (lambda p: "total revenue" in p["question"], {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "total_revenue", "answer_value": "$34,229 million", "answer_unit": "millions"}],
            "answer_text": "<ENT_1>'s total revenue was $34,229 million.", "used_fff_ids": [real_ffts_rev[0].fff_id],
        }),
    ])

    # Two separate extraction calls now, one per sub-answer, not one
    # pooled response holding both claims.
    claim_extraction_client = _ContentKeyedClient([
        (lambda p: "net income" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> net income (FY2022): $5,777 million",
             "entity": "<ENT_1>", "metric": "net_income", "period_label": "FY2022",
             "asserted_value": "5777", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
        (lambda p: "total revenue" in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": "<ENT_1> total revenue (FY2022): $34,229 million",
             "entity": "<ENT_1>", "metric": "total_revenue", "period_label": "FY2022",
             "asserted_value": "34229", "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]}),
    ])
    # claim 1 genuinely matches, claim 2 spuriously conflicts despite real
    # supporting evidence -- a real disagreement, not a pooling artifact.
    independent_verification_client = _ContentKeyedClient([
        (lambda p: "net_income" in p["verification_question"], {
            "reasoning": "...", "supported": True, "answer_value": "5777", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_ni[0].fff_id],
        }),
        (lambda p: "total_revenue" in p["verification_question"], {
            "reasoning": "...", "supported": True, "answer_value": "99999", "answer_unit": "USD",
            "period_label": "FY2022", "scale": "millions", "supporting_fff_ids": [real_ffts_rev[0].fff_id],
        }),
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


class _TrackingMetadataStore:
    """Like _FakeMetadataStore, but records every call -- proves the
    config flag actually reaches the real metadata query (step 5.2, C4+),
    not just metadata_filter.py's own unit tests in isolation."""

    def __init__(self, propositions=()):
        self._propositions = list(propositions)
        self.calls: list[dict] = []

    def query(self, company=None, fiscal_year=None, quarter=None, filing_type=None, metrics=None):
        self.calls.append({"company": company, "metrics": metrics})
        return list(self._propositions)


def test_config_enable_metric_filter_reaches_the_real_metadata_query():
    """C4+ (decision 12) is C4 with retrieval.enable_metric_filter: true --
    this is the one line config/comparison/c4_plus.yaml adds over
    config/experiments/c4_full.yaml, so it must actually change what C4's
    own pipeline sends to the metadata store."""
    store = _TrackingMetadataStore([])
    bundle = _FakeBundle(store, [])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []}])

    config = _config()
    config_with_metric_filter = config.model_copy(
        update={"retrieval": config.retrieval.model_copy(update={"enable_metric_filter": True})}
    )

    answer_question(
        "What was 3M's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        config_with_metric_filter, registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert store.calls, "metadata store was never queried"
    assert store.calls[0]["metrics"] == ["net_income"]


def test_config_metric_filter_off_by_default_omits_metrics_from_the_query():
    """Same question, but with the plain C4 config (enable_metric_filter
    defaults to False) -- the metadata query must not be metric-constrained."""
    store = _TrackingMetadataStore([])
    bundle = _FakeBundle(store, [])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []}])

    answer_question(
        "What was 3M's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert store.calls, "metadata store was never queried"
    assert store.calls[0]["metrics"] is None


# --- decision 21, fix 1: "not provided" filler sub-answers must not reach CoVe ---


def test_filler_not_provided_sub_answer_never_reaches_cove_but_stays_in_answer_text():
    """Mirrors the real `fbc_ext2_entity_0001` failure: the splitter's own
    Cartesian product makes a combo nobody asked about ("Adobe net_income"),
    retrieval genuinely finds nothing for it, and the real 3M sub-answer is
    retrieved and verified fine. Before fix 1 this abstained the whole
    answer; after it, the filler must (a) never become a claim CoVe sees,
    (b) still show up as "not provided" in the final text, and (c) not
    cause abstention."""
    registry_with_adobe = list(REGISTRY) + [
        PlaceholderRegistryEntry(surface_form="Adobe", placeholder="<ENT_2>", canonical=CanonicalIdentifiers(ticker="ADBE")),
    ]
    prop_ni = _make_proposition("p_ni", "3M_2022_10K", page=47, text="3M had net income of $5,777 million for the year ended December 31, 2022.", company="3M")
    bundle = _FakeBundle(_CompanyAwareFakeMetadataStore([prop_ni]), [prop_ni])

    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M", "Adobe"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_response = {"reasoning": "...", "facts": [
        {"proposition_id": "p_ni", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,777 million"},
    ]}
    real_ffts = rewrite_to_ffts("q", [prop_ni], _SequencedClient([fff_response]), subject_override="<ENT_1>")
    # Adobe's sub-query finds nothing -- its own fff_client call returns no facts.
    fff_client = _SequencedClient([fff_response, {"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts[0].fff_id]},
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
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
        "What was 3M's net income for FY2022, and what was Adobe's net income for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=registry_with_adobe,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    # (a) CoVe only ever saw the one real claim -- the draft handed to claim
    # extraction never contained "not provided" text to extract a claim from.
    draft_seen_by_cove = claim_extraction_client.calls[0]["draft"]
    assert "not provided" not in draft_seen_by_cove
    assert len(result.verification_trace) == 1
    # (b) the final answer text still honestly shows the gap.
    assert "not provided" in result.answer_text
    # (c) the filler no longer drags the whole answer into abstention.
    assert result.abstained is False
    assert {a.value for a in result.answers} == {"$5,777 million"}


def test_a_single_sub_query_genuine_gap_still_abstains_exactly_as_today():
    """The short-circuit path for `not all_ffts or not any_answerable`
    (a genuinely unanswerable, single-sub-query question) must be
    untouched by fix 1 -- CoVe is still never invoked and the answer
    still abstains."""
    bundle = _FakeBundle(_FakeMetadataStore([]), [])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["net_income"], "periods": [{"fiscal_year": 2099}]}]
    )
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []}])
    claim_extraction_client = _SequencedClient([])

    result = answer_question(
        "What was 3M's net income for FY2099?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        claim_extraction_client, _SequencedClient([]), _SequencedClient([]),
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.abstained is True
    assert len(claim_extraction_client.calls) == 0


# --- decision 26: on-demand metric labeling, moved to the reranked
# candidates (see online/pipeline_c4.py's own comment on why this moved out
# of online/retrieval/metadata_filter.py) -- wired through the real pipeline.


class _UpsertTrackingMetadataStore(_CompanyAwareFakeMetadataStore):
    """Like _CompanyAwareFakeMetadataStore, but records every upsert so a
    test can confirm the fresh label was actually persisted, not just
    handed back in memory."""

    def __init__(self, propositions):
        super().__init__(propositions)
        self.upserted: list = []

    def upsert(self, proposition):
        self.upserted.append(proposition)
        for i, p in enumerate(self._propositions):
            if p.proposition_id == proposition.proposition_id:
                self._propositions[i] = proposition
                return
        self._propositions.append(proposition)


def _unlabelled_capex_prop() -> Proposition:
    # Deliberately not any of DEFAULT_METRIC_ALIASES's phrases, so the free
    # rule pass finds nothing and the fallback client is the only way this
    # gets labelled.
    return _make_proposition(
        "p_capex", "3M_2022_10K", page=10, text="3M Company's board approved a capital initiative for FY2022.",
        company="3M Company",
    )


def _rule_labellable_prop() -> Proposition:
    # Text that the free rules DO match ("accounts receivable") -- a model
    # call here would be a bug, not just wasted spend.
    return _make_proposition(
        "p_ar", "3M_2022_10K", page=11, text="3M Company reported accounts receivable for FY2022.",
        company="3M Company",
    )


def _config_with_ondemand_labeling(enabled: bool) -> ExperimentConfig:
    config = _config()
    return config.model_copy(
        update={"retrieval": config.retrieval.model_copy(update={"enable_ondemand_metric_labeling": enabled})}
    )


def _run_capex_question(bundle, config, label_client):
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["capital_expenditure"], "periods": [{"fiscal_year": 2022}]}]
    )
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient(
        [{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []}]
    )
    return answer_question(
        "What was 3M's capital expenditure for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        config, registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
        metric_label_llm_client=label_client,
    )


def test_ondemand_metric_labeling_off_by_default_never_calls_the_label_client_even_if_one_is_passed():
    """The config flag, not merely a client being configured, gates this --
    passing a client with the flag left at its False default must be a
    pure no-op, same as every other optional call site's safe default."""
    prop = _unlabelled_capex_prop()
    store = _UpsertTrackingMetadataStore([prop])
    bundle = _FakeBundle(store, [prop])
    label_client = _SequencedClient([])  # would raise IndexError if ever called

    _run_capex_question(bundle, _config_with_ondemand_labeling(enabled=False), label_client)

    assert len(label_client.calls) == 0
    assert store.upserted == []
    assert prop.metrics == ()


def test_ondemand_metric_labeling_with_no_client_passed_is_a_byte_for_byte_noop():
    """The other half of the default no-op guarantee: flag on, but no
    client given (the real default before any caller wires one up)."""
    prop = _unlabelled_capex_prop()
    store = _UpsertTrackingMetadataStore([prop])
    bundle = _FakeBundle(store, [prop])

    _run_capex_question(bundle, _config_with_ondemand_labeling(enabled=True), label_client=None)

    assert store.upserted == []
    assert prop.metrics == ()


def test_ondemand_metric_labeling_on_labels_the_candidate_and_persists_it_through_the_real_pipeline():
    prop = _unlabelled_capex_prop()
    store = _UpsertTrackingMetadataStore([prop])
    bundle = _FakeBundle(store, [prop])
    label_client = _SequencedClient([{"reasoning": "...", "metrics": ["capital_expenditure"]}])

    _run_capex_question(bundle, _config_with_ondemand_labeling(enabled=True), label_client)

    assert len(label_client.calls) == 1
    assert len(store.upserted) == 1
    assert store.upserted[0].metrics == ("capital_expenditure",)
    assert store._propositions[0].metrics == ("capital_expenditure",)  # persisted in the store itself


def test_ondemand_metric_labeling_rules_first_gate_respected_model_never_reached():
    """A fact the free rules can label outright must never reach the
    fallback client -- the same rules-first gate backfill_metrics.py uses."""
    prop = _rule_labellable_prop()
    store = _UpsertTrackingMetadataStore([prop])
    bundle = _FakeBundle(store, [prop])
    label_client = _SequencedClient([])  # would raise IndexError if ever called

    _run_capex_question(bundle, _config_with_ondemand_labeling(enabled=True), label_client)

    assert len(label_client.calls) == 0
    assert store.upserted[0].metrics == ("accounts_receivable",)


def test_ondemand_metric_labeling_persists_so_a_second_identical_call_never_relabels():
    """Once persisted, a second request against the same store never
    re-invokes the model for the same proposition."""
    prop = _unlabelled_capex_prop()
    store = _UpsertTrackingMetadataStore([prop])
    bundle = _FakeBundle(store, [prop])
    first_client = _SequencedClient([{"reasoning": "...", "metrics": ["capital_expenditure"]}])

    _run_capex_question(bundle, _config_with_ondemand_labeling(enabled=True), first_client)
    assert len(first_client.calls) == 1

    second_client = _SequencedClient([])  # would raise IndexError if called
    _run_capex_question(bundle, _config_with_ondemand_labeling(enabled=True), second_client)

    assert len(second_client.calls) == 0


def test_ondemand_metric_labeling_fresh_label_reaches_the_near_identical_check_same_call():
    """The whole point of decision 26: a candidate labelled on the spot
    must be visible to decision 23's near-identical check in the SAME
    request, not a future one. Two General-Mills-shaped receivables facts,
    same period and basis, one already labelled and one not -- once the
    fallback client labels the second with the same canonical metric, the
    metric-wording collision must fire right away."""
    prop_labelled = _make_numeric_proposition(
        "p_ar_customers", "gm_2022_10K", page=20,
        text="3M Company had customers receivables of $1,720.4 million for FY2022.",
        company="3M Company", value=1720.4, fiscal_year=2022,
    ).model_copy(update={"metrics": ("accounts_receivable",)})
    prop_unlabelled = _make_numeric_proposition(
        "p_ar_total", "gm_2022_10K", page=21,
        text="3M Company had total receivables of $1,692.1 million for FY2022.",
        company="3M Company", value=1692.1, fiscal_year=2022,
    )
    assert prop_unlabelled.metrics == ()  # rules genuinely find nothing on this phrase

    store = _UpsertTrackingMetadataStore([prop_labelled, prop_unlabelled])
    bundle = _FakeBundle(store, [prop_labelled, prop_unlabelled])
    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": ["accounts_receivable"], "periods": [{"fiscal_year": 2022}]}]
    )
    # Both versions come back "not answerable" -- keeps this test to the
    # retrieval/labeling wiring, not CoVe, which short-circuits entirely
    # when nothing is answerable (see answer_question's own "not all_ffts
    # or not any_answerable" branch).
    fff_client = _SequencedClient([{"reasoning": "...", "facts": []}, {"reasoning": "...", "facts": []}])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
    ])
    label_client = _SequencedClient([{"reasoning": "...", "metrics": ["accounts_receivable"]}])

    result = answer_question(
        "What was 3M's accounts receivable for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, fff_client, generator_client,
        _SequencedClient([]), _SequencedClient([]), _SequencedClient([]),
        _config_with_ondemand_labeling(enabled=True), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
        metric_label_llm_client=label_client,
    )

    assert len(label_client.calls) == 1
    assert store._propositions[1].metrics == ("accounts_receivable",)  # persisted
    assert result.near_identical_detected is True
    assert result.near_identical_detail == "metric_wording"


# --- decision 38: parallel sub-query / CoVe loops ---


def _four_metric_fixture():
    """Four sub-queries, each with its own single real proposition and a
    distinguishable metric -- enough fan-out to make a real, measurable
    difference between running them one at a time and running them
    concurrently."""
    metrics = ["net_income", "total_revenue", "operating_margin", "gross_margin"]
    values = {"net_income": "1000", "total_revenue": "2000", "operating_margin": "3000", "gross_margin": "4000"}
    props = {
        m: _make_proposition(
            f"p_{m}", "3M_2022_10K", page=i,
            text=f"3M Company had {m.replace('_', ' ')} of ${values[m]} million for FY2022.",
            company="3M Company",
        )
        for i, m in enumerate(metrics)
    }
    bundle = _FakeBundle(_FakeMetadataStore(list(props.values())), list(props.values()))

    splitter_client = _SequencedClient(
        [{"reasoning": "...", "entities": ["3M"], "metrics": metrics, "periods": [{"fiscal_year": 2022}]}]
    )

    fff_responses = {}
    real_ffts_by_metric = {}
    for m in metrics:
        prop = props[m]
        value_text = f"${values[m]} million"
        response = {"reasoning": "...", "facts": [
            {"proposition_id": prop.proposition_id, "subject": "<ENT_1>", "metric": m,
             "period_label": "FY2022", "value_text": value_text},
        ]}
        fff_responses[m] = response
        real_ffts_by_metric[m] = rewrite_to_ffts(
            "q", [prop], _SequencedClient([response]), subject_override="<ENT_1>"
        )

    # Keyed by the rendered question text (each sub-query's own metric,
    # space-separated), not by candidate propositions/fff_ids -- the fake
    # metadata store returns every proposition for every sub-query, so the
    # candidate list alone can't tell them apart.
    fff_client = _ContentKeyedClient([
        (lambda p, phrase=m.replace("_", " "): phrase in p["sub_query"]["question"], fff_responses[m])
        for m in metrics
    ])
    generator_client = _ContentKeyedClient([
        (lambda p, phrase=m.replace("_", " "): phrase in p["question"], {
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": m, "answer_value": f"${values[m]} million", "answer_unit": "millions"}],
            "answer_text": f"<ENT_1>'s {m.replace('_', ' ')} was ${values[m]} million.",
            "used_fff_ids": [real_ffts_by_metric[m][0].fff_id],
        })
        for m in metrics
    ])
    claim_extraction_client = _ContentKeyedClient([
        (lambda p, m=m: m.replace("_", " ") in p["draft"], {"reasoning": "...", "claims": [
            {"claim_id": "c_1", "claim_text": f"<ENT_1> {m} (FY2022): ${values[m]} million",
             "entity": "<ENT_1>", "metric": m, "period_label": "FY2022",
             "asserted_value": values[m], "asserted_unit": "USD", "scale": "millions", "is_numeric": True},
        ]})
        for m in metrics
    ])
    independent_verification_client = _ContentKeyedClient([
        (lambda p, m=m: m in p["verification_question"], {
            "reasoning": "...", "supported": True, "answer_value": values[m],
            "answer_unit": "USD", "period_label": "FY2022", "scale": "millions",
            "supporting_fff_ids": [real_ffts_by_metric[m][0].fff_id],
        })
        for m in metrics
    ])
    prose_comparison_client = _SequencedClient([])

    return (
        metrics, bundle, splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
    )


def test_parallel_loops_give_a_real_wall_clock_speedup_over_sequential_cost():
    """The actual point of decision 38: with an artificial per-call delay
    standing in for real LLM latency, four sub-queries' retrieval/FFF/
    generation (loop 1) and their four sub-answers' CoVe verification
    (loop 2) must finish in well under the time four full sequential
    passes would take -- proving the two loops really overlap their LLM
    calls instead of queuing them one at a time."""
    (
        metrics, bundle, splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
    ) = _four_metric_fixture()

    delay = 0.15
    delayed_fff = _DelayedClient(fff_client, delay)
    delayed_generator = _DelayedClient(generator_client, delay)
    delayed_claim_extraction = _DelayedClient(claim_extraction_client, delay)
    delayed_independent_verification = _DelayedClient(independent_verification_client, delay)

    # Sequential cost would be 4 sub-queries x (fff + generate) delay for
    # loop 1, plus 4 sub-answers x (claim-extraction + independent-verify)
    # delay for loop 2 -- 4*0.3 + 4*0.3 = 2.4s. Parallel (<=6 workers, only
    # 4 items) should cost close to one round of each: ~0.3 + 0.3 = 0.6s.
    start = time.perf_counter()
    result = answer_question(
        "What were 3M's net income, total revenue, operating margin and gross margin for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, delayed_fff, delayed_generator,
        delayed_claim_extraction, delayed_independent_verification, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )
    elapsed = time.perf_counter() - start

    assert result.sub_query_count == 4
    assert result.abstained is False
    assert len(result.verification_trace) == 4
    # Generous bound: sequential would be ~2.4s, parallel should be
    # comfortably under half that even with scheduling/test overhead.
    assert elapsed < 1.5, f"parallel loops took {elapsed:.2f}s -- expected well under the ~2.4s sequential cost"


def test_parallel_sub_query_order_survives_a_deliberately_reversed_completion_order():
    """Decision 38's own correctness requirement: the final result must be
    identical to what the sequential loop would have produced regardless
    of which sub-query's thread actually finishes first. Delays the FIRST
    sub-query (net income) so the second (total revenue) almost certainly
    finishes its work before it -- `pieces`/`answers` must still come out
    in sub-query order (net income first), because results are merged by
    `executor.map`'s input order, never completion order."""
    (
        metrics, bundle, splitter_client, fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
    ) = _four_metric_fixture()

    # Only delay the first sub-query's own calls -- it will finish last
    # despite being submitted first.
    def _is_net_income_fff(payload):
        return "net income" in payload["sub_query"]["question"]

    selectively_delayed_fff = _SelectivelyDelayedClient(fff_client, _is_net_income_fff, 0.2)

    result = answer_question(
        "What were 3M's net income, total revenue, operating margin and gross margin for FY2022?",
        bundle, FakeEmbedder(dimension=8), _FakeReranker(),
        splitter_client, selectively_delayed_fff, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        _config(), registry=REGISTRY,
        fff_rewriter_model="m", generator_model="m", query_splitter_model="m", cove_verification_model="gpt-5.6-luna",
    )

    assert result.sub_query_count == 4
    assert result.abstained is False
    # Sub-query order (net income, total revenue, operating margin, gross
    # margin), exactly as the splitter produced them -- not completion order.
    assert "net income" in result.pieces[0]
    assert "total revenue" in result.pieces[1]
    assert "operating margin" in result.pieces[2]
    assert "gross margin" in result.pieces[3]
    answer_metrics_in_order = [a.metric for a in result.answers]
    assert answer_metrics_in_order == metrics
