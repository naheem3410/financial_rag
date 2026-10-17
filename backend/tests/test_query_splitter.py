"""Query splitter tests."""
from __future__ import annotations

from contracts.models import Period, RequestContext, SubQuery
from online.analysis.query_splitter import LLMQuerySplitter, PassthroughQueryTransformer, render_subquery_question


class _FakeLLMClient:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._response


def _ctx() -> RequestContext:
    return RequestContext(request_id="r_1", config_hash="cfg_abc")


def test_passthrough_returns_exactly_one_sub_query():
    transformer = PassthroughQueryTransformer()

    result = transformer.transform("What was Acme Corp's operating margin in Q3 FY2024?", _ctx())

    assert len(result) == 1


def test_split_is_the_full_cartesian_product_of_entities_metrics_periods():
    """1 entity x 2 metrics x 2 periods = 4, provable by construction since it's all code."""
    client = _FakeLLMClient(
        {
            "reasoning": "...",
            "entities": ["Acme Corp"],
            "metrics": ["operating_margin", "net_profit_margin"],
            "unmapped_metrics": [],
            "periods": [{"fiscal_year": 2024, "quarter": 3}, {"fiscal_year": 2024, "quarter": 4}],
            "filing_types": [],
        }
    )
    splitter = LLMQuerySplitter(llm_client=client)

    result = splitter.transform("How did Acme Corp's operating margin and net margin compare?", _ctx())

    assert len(result) == 4
    combos = {(sq.entity, sq.metric, (sq.period.fiscal_year, sq.period.quarter)) for sq in result}
    assert combos == {
        ("Acme Corp", "operating_margin", (2024, 3)),
        ("Acme Corp", "operating_margin", (2024, 4)),
        ("Acme Corp", "net_profit_margin", (2024, 3)),
        ("Acme Corp", "net_profit_margin", (2024, 4)),
    }


def test_each_sub_query_has_exactly_one_entity_metric_period():
    """No collision pair ever co-occurs in a single sub-query, by construction not runtime check."""
    client = _FakeLLMClient(
        {
            "reasoning": "...",
            "entities": ["Acme Corp", "Beta Inc"],
            "metrics": ["operating_margin"],
            "unmapped_metrics": [],
            "periods": [{"fiscal_year": 2024, "quarter": 3}],
            "filing_types": [],
        }
    )
    splitter = LLMQuerySplitter(llm_client=client)

    result = splitter.transform("Compare Acme Corp and Beta Inc operating margin.", _ctx())

    assert len(result) == 2
    for sq in result:
        assert sq.entity in {"Acme Corp", "Beta Inc"}
        assert sq.metric == "operating_margin"


def test_empty_entities_falls_back_to_single_sub_query():
    client = _FakeLLMClient(
        {"reasoning": "...", "entities": [], "metrics": ["operating_margin"], "periods": []}
    )
    splitter = LLMQuerySplitter(llm_client=client)

    result = splitter.transform("What was the operating margin?", _ctx())

    assert len(result) == 1


def test_empty_metrics_falls_back_to_single_sub_query():
    client = _FakeLLMClient({"reasoning": "...", "entities": ["Acme Corp"], "metrics": [], "periods": []})
    splitter = LLMQuerySplitter(llm_client=client)

    result = splitter.transform("Tell me about Acme Corp.", _ctx())

    assert len(result) == 1


def test_no_period_mentioned_yields_one_unconstrained_period_slot():
    client = _FakeLLMClient(
        {"reasoning": "...", "entities": ["Acme Corp"], "metrics": ["operating_margin"], "periods": []}
    )
    splitter = LLMQuerySplitter(llm_client=client)

    result = splitter.transform("What is Acme Corp's operating margin?", _ctx())

    assert len(result) == 1


def test_unmapped_metrics_do_not_block_the_split():
    client = _FakeLLMClient(
        {
            "reasoning": "...",
            "entities": ["Acme Corp"],
            "metrics": ["operating_margin"],
            "unmapped_metrics": ["ebitda"],
            "periods": [{"fiscal_year": 2024, "quarter": 3}],
        }
    )
    splitter = LLMQuerySplitter(llm_client=client)

    result = splitter.transform("What was Acme Corp's operating margin and EBITDA?", _ctx())

    assert len(result) == 1
    assert result[0].metric == "operating_margin"


def test_splitter_issues_exactly_one_llm_call(llm_call_counter):
    client = _FakeLLMClient(
        {"reasoning": "...", "entities": ["Acme Corp"], "metrics": ["operating_margin"], "periods": []}
    )
    splitter = LLMQuerySplitter(llm_client=client)

    splitter.transform("What is Acme Corp's operating margin?", _ctx())

    assert len(client.calls) == 1


def test_split_happens_regardless_of_query_analyzer_collision_detection():
    """Splitting is driven by slot cardinality alone, never gated on collision detection.
    Module never imports query_analyzer, so a lexicon miss there can't disable NPCE."""
    import online.analysis.query_splitter as query_splitter_module

    assert "query_analyzer" not in query_splitter_module.__dict__
    assert not any("analyze" in name.lower() for name in dir(query_splitter_module))

    # cardinality > 1 splits even though nothing here supplied collision info
    client = _FakeLLMClient(
        {
            "reasoning": "...",
            "entities": ["Acme Corp"],
            "metrics": ["operating_margin", "net_profit_margin"],
            "periods": [],
        }
    )
    splitter = LLMQuerySplitter(llm_client=client)

    result = splitter.transform("Compare operating and net margin.", _ctx())

    assert len(result) == 2


def test_render_subquery_question_uses_entity_verbatim():
    """Entity has already been through the placeholder remapper by the time
    this runs, so rendering it verbatim keeps the prompt consistent with the FFF's subject."""
    sq = SubQuery(
        sub_query_id="sq_1", entity="<ENT_1>", metric="net_income",
        period=Period(fiscal_year=2022), period_label="FY2022",
    )

    assert render_subquery_question(sq) == "What was <ENT_1>'s net income for FY2022?"


def test_render_subquery_question_no_period():
    sq = SubQuery(
        sub_query_id="sq_1", entity="<ENT_1>", metric="operating_margin",
        period=Period(fiscal_year=0), period_label="",
    )

    assert render_subquery_question(sq) == "What was <ENT_1>'s operating margin?"


# --- step 2.2: free-text metrics via the dictionary, for phrases outside the fixed lexicon ---


def _response(unmapped, metrics=None):
    return {
        "reasoning": "...", "entities": ["Acme Corp"], "metrics": metrics or [],
        "unmapped_metrics": unmapped, "periods": [{"fiscal_year": 2024}], "filing_types": [],
    }


def test_an_unmapped_phrase_the_dictionary_knows_is_resolved_into_a_real_metric():
    client = _FakeLLMClient(_response(unmapped=["net sales"]))
    splitter = LLMQuerySplitter(client, dictionary_lookup=lambda phrase: {"net sales": "total_revenue"}.get(phrase))

    result = splitter.transform("What were Acme's net sales?", _ctx())

    assert [sq.metric for sq in result] == ["total_revenue"]


def test_an_unmapped_phrase_the_dictionary_does_not_know_falls_back_to_passthrough():
    client = _FakeLLMClient(_response(unmapped=["some brand new phrase"]))
    splitter = LLMQuerySplitter(client, dictionary_lookup=lambda phrase: None)

    result = splitter.transform("some question", _ctx())

    assert len(result) == 1
    assert result[0].entity == "some question"  # passthrough, not a real split


def test_an_unknown_phrase_is_logged_for_future_dictionary_growth():
    logged = []
    client = _FakeLLMClient(_response(unmapped=["some brand new phrase"]))
    splitter = LLMQuerySplitter(client, dictionary_lookup=lambda phrase: None, unmatched_logger=logged.append)

    splitter.transform("some question", _ctx())

    assert logged == ["some brand new phrase"]


def test_a_resolved_phrase_is_not_logged_as_unmatched():
    logged = []
    client = _FakeLLMClient(_response(unmapped=["net sales"]))
    splitter = LLMQuerySplitter(
        client, dictionary_lookup=lambda phrase: "total_revenue", unmatched_logger=logged.append
    )

    splitter.transform("some question", _ctx())

    assert logged == []


def test_resolved_metric_joins_the_ones_the_model_already_mapped():
    client = _FakeLLMClient(_response(unmapped=["net sales"], metrics=["net_income"]))
    splitter = LLMQuerySplitter(client, dictionary_lookup=lambda phrase: "total_revenue")

    result = splitter.transform("Acme's net income and net sales?", _ctx())

    assert {sq.metric for sq in result} == {"net_income", "total_revenue"}


def test_a_resolved_metric_already_in_the_model_output_is_not_duplicated():
    client = _FakeLLMClient(_response(unmapped=["net sales"], metrics=["total_revenue"]))
    splitter = LLMQuerySplitter(client, dictionary_lookup=lambda phrase: "total_revenue")

    result = splitter.transform("Acme's net sales?", _ctx())

    assert [sq.metric for sq in result] == ["total_revenue"]


def test_no_dictionary_or_logger_configured_behaves_exactly_like_before_this_step():
    client = _FakeLLMClient(_response(unmapped=["some phrase"], metrics=["net_income"]))
    splitter = LLMQuerySplitter(client)  # no dictionary_lookup, no unmatched_logger

    result = splitter.transform("some question", _ctx())  # must not raise

    assert [sq.metric for sq in result] == ["net_income"]


def test_multiple_unmapped_phrases_are_each_resolved_independently():
    client = _FakeLLMClient(_response(unmapped=["net sales", "widgets sold"]))
    lookup = {"net sales": "total_revenue"}.get
    logged = []
    splitter = LLMQuerySplitter(client, dictionary_lookup=lookup, unmatched_logger=logged.append)

    result = splitter.transform("some question", _ctx())

    assert {sq.metric for sq in result} == {"total_revenue"}
    assert logged == ["widgets sold"]
