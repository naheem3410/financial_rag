import threading

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api.server import Runtime, app, build_registry, build_response, make_name_restorer  # noqa: E402
from contracts.models import RequestContext  # noqa: E402
from offline.registry.entities import Entity  # noqa: E402
from online.output.generator import AnswerValue  # noqa: E402
from online.pipeline_c4 import C4AnswerResult  # noqa: E402
from online.placeholders.remapper import remap_entity  # noqa: E402

COMPANIES = [
    Entity(company="3M Company", ticker=None, version=1),
    Entity(company="Activision Blizzard, Inc.", ticker=None, version=1),
    Entity(company="Adobe Systems Incorporated", ticker=None, version=1),
]


@pytest.mark.parametrize(
    "surface, full_name",
    [
        ("3M", "3M Company"),
        ("3M's", "3M Company"),
        ("3M Company", "3M Company"),
        ("Activision", "Activision Blizzard, Inc."),
        ("Adobe", "Adobe Systems Incorporated"),
    ],
)
def test_short_names_resolve_to_the_full_indexed_company_name(surface, full_name):
    # the metadata filter matches on the full name, so a longer alias would silently retrieve nothing
    registry = build_registry(COMPANIES)
    ctx = RequestContext(request_id="t", config_hash="t")
    placeholder = remap_entity(surface, ctx, registry=registry)
    assert ctx.placeholder_map[placeholder] == full_name


def test_a_short_name_shared_by_two_companies_is_not_guessed():
    registry = build_registry([
        Entity(company="General Mills, Inc.", ticker=None, version=1),
        Entity(company="General Electric Company", ticker=None, version=1),
    ])
    assert "General" not in {e.surface_form for e in registry}


def test_restorer_swaps_known_placeholders_and_leaves_unknown_ones():
    restore = make_name_restorer(build_registry(COMPANIES))
    assert restore("<ENT_1> accounts payable") == "3M Company accounts payable"
    assert restore("<ENT_99> is unknown") == "<ENT_99> is unknown"


# --- /api/health and /api/info: no pipeline, no database, just the
# runtime's static info dict, so a bare Runtime stub is enough. ---


@pytest.fixture
def info_client():
    rt = Runtime(
        config=None, embedder=None, reranker=None, bundle=None, registry=[], clients={}, models={},
        restore_names=lambda s: s, lock=threading.Lock(),
        info={
            "pipeline": "c4_full",
            "companies": ["3M Company"],
            "documents": ["3M_2018_10K"],
            "proposition_count": 42,
            "models": {"generator": "m"},
            "document_types": ["10-K"],
        },
    )
    app.state.runtime = rt
    return TestClient(app)


def test_health_endpoint_returns_ok(info_client):
    response = info_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_info_endpoint_returns_the_runtimes_info_dict(info_client):
    response = info_client.get("/api/info")
    assert response.status_code == 200
    body = response.json()
    assert body["pipeline"] == "c4_full"
    assert body["companies"] == ["3M Company"]
    assert body["proposition_count"] == 42


def _result(**overrides):
    base = dict(
        question="q", abstained=False, abstention_message="", answerable=True,
        answers=[AnswerValue(metric="accounts payable", value="2,266", unit="million USD")],
        answer_text="<ENT_1> accounts payable was 2,266.",
        citations=[{
            "proposition_id": "p1", "source_uri": "3M_2018_10K", "filing_type": "10-K",
            "filing_date": "2018-12-31", "page": 57, "provenance_tier": "retrieved",
        }],
        used_fff_ids=["f1"], sub_query_count=1, fff_count=1,
        query_splitter_model="m", fff_rewriter_model="m", generator_model="m", cove_verification_model="m",
        verification_trace=[
            {"claim_id": "C1", "claim_text": "<ENT_1> accounts payable: 2,266", "is_numeric": True,
             "verdict": "match", "supporting_fff_ids": ["f1"]},
        ],
        confidence_score=1.0,
    )
    base.update(overrides)
    return C4AnswerResult(**base)


def test_answered_response_shapes_citations_and_score():
    restore = make_name_restorer(build_registry(COMPANIES))
    out = build_response(_result(), {"generator": "m"}, restore, lambda pid: "evidence text", 1234)
    assert out.status == "answered"
    assert out.answer_text == "3M Company accounts payable was 2,266."
    assert out.citations[0].page_index == 57 and out.citations[0].page_number == 58
    assert out.citations[0].evidence == "evidence text"
    assert out.verification.verified_count == 1 and out.verification.score == 1.0
    assert out.pipeline.latency_ms == 1234


def test_abstained_response_reports_zero_verified():
    result = _result(
        abstained=True, abstention_message="not supported", answerable=False, answers=[], citations=[],
        verification_trace=[
            {"claim_id": "c_1", "claim_text": "q", "is_numeric": True, "verdict": "unsupported",
             "supporting_fff_ids": []},
        ],
        confidence_score=0.0,
    )
    out = build_response(result, {}, lambda s: s, lambda pid: "", 10)
    assert out.status == "abstained"
    assert out.abstention_message == "not supported"
    assert out.verification.verified_count == 0 and out.verification.score == 0.0
    assert out.citations == []


# --- step 4.7: "How it happened" ---


def test_how_it_happened_is_included_in_the_response():
    result = _result(
        routing_road="split_money", routing_reason='the question contains "and"',
        pieces=("What was 3M's net income?", "What was 3M's total revenue?"),
        question_understood="What were 3M's net income and total revenue?",
        retry_attempted=True, retry_names_tried=("3M Company",),
    )

    out = build_response(result, {}, lambda s: s, lambda pid: "", 10)

    assert out.how_it_happened.road == "split_money"
    assert out.how_it_happened.pieces == ["What was 3M's net income?", "What was 3M's total revenue?"]
    assert out.how_it_happened.retry_names_tried == ["3M Company"]
    assert out.how_it_happened.several_versions_note is None
    assert "2 pieces" in out.how_it_happened.summary


def test_how_it_happened_is_present_even_when_abstained():
    result = _result(
        abstained=True, abstention_message="not supported", answerable=False, answers=[], citations=[],
        verification_trace=[
            {"claim_id": "c_1", "claim_text": "q", "is_numeric": True, "verdict": "unsupported", "supporting_fff_ids": []},
        ],
    )

    out = build_response(result, {}, lambda s: s, lambda pid: "", 10)

    assert out.how_it_happened is not None
    assert out.how_it_happened.summary


def test_how_it_happened_shows_the_steps():
    step_plan = (
        {"step_id": 1, "question": "Which company had the highest revenue?", "answer_text": "3M did.", "answerable": True},
    )
    result = _result(routing_road="step_by_step", routing_reason="...", step_plan=step_plan, pieces=("Which company had the highest revenue?",))

    out = build_response(result, {}, lambda s: s, lambda pid: "", 10)

    assert len(out.how_it_happened.steps) == 1
    assert out.how_it_happened.steps[0].question == "Which company had the highest revenue?"
    assert out.how_it_happened.checked_against_original_question is False
