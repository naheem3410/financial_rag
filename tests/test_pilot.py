"""Pilot indexing runner tests. `build_llm_client` is monkeypatched to a
fake, no real network/API calls, no real spend.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.schema import ExperimentConfig
from llm.adapters.base import AdapterResponse, LLMCallFailed, LLMInfrastructureError
from offline.pilot import DocumentPilotResult, PilotReport, SpendCapExceeded, estimate_cost_usd, run_pilot

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "path_a"

_ALL_CALL_SITES = {
    "proposition_segmenter": {"provider": "openai", "model": "gpt-4.1-mini-2025-04-14", "include_reasoning_field": False},
    "numeric_qualifier_resolver": {"provider": "openai", "model": "m-2025-01-01"},
    "query_splitter": {"provider": "openai", "model": "m-2025-01-01"},
    "fff_rewriter": {"provider": "openai", "model": "m-2025-01-01"},
    "generator": {"provider": "openai", "model": "m-2025-01-01"},
    "cove_verification": {"provider": "openai", "model": "m-2025-01-01"},
}


def _config() -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "experiment_id": "c1_baseline",
            "enable_npce": False,
            "enable_cove": False,
            "retrieval": {"mode": "hybrid", "top_k": 20, "rerank_top_k": 5, "enable_reranker": True, "time_weighting": True},
            "indexing": {"granularity": "proposition"},
            "llm": {
                "defaults": {"temperature": 0.0, "structured_output": True},
                "call_sites": _ALL_CALL_SITES,
                "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
            },
            "corpus": {"pdf_dir": str(FIXTURES), "required_docs_file": "data/required_docs.txt"},
        }
    )


class _FakeRoutedLLMClient:
    def __init__(self, responses, input_tokens=1000, output_tokens=200):
        self._responses = list(responses)
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.last_response_meta = None
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        self.last_response_meta = AdapterResponse(
            data={}, seed_supported=False, reasoning_tokens=None,
            input_tokens=self._input_tokens, output_tokens=self._output_tokens,
            adapter_version="fake-1.0",
        )
        return self._responses.pop(0) if self._responses else {"propositions": []}


def test_run_pilot_processes_a_document_and_tracks_usage(tmp_path, monkeypatch):
    fake_client = _FakeRoutedLLMClient(
        [
            {"propositions": [{"text": "3M Company reported net sales of $32.2 billion for fiscal year 2018. The increase was driven by organic growth across all business segments.", "source_offset_hint": None}]},
            {"propositions": [{"text": "Acme Corp operating margin was 16.5%.", "source_offset_hint": None}]},
        ]
    )
    monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)

    report = run_pilot(
        ["sample"], _config(), checkpoint_path=tmp_path / "checkpoint.json", max_spend_usd=10.0
    )

    assert len(report.documents) == 1
    doc = report.documents[0]
    assert doc.doc_name == "sample"
    assert doc.pages_total == 2
    assert doc.llm_requests == fake_client.calls
    assert doc.input_tokens == fake_client.calls * 1000
    assert doc.cost_usd > 0


def test_checkpoint_resumes_and_does_not_recall_llm_for_completed_docs(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "checkpoint.json"
    fake_client = _FakeRoutedLLMClient([{"propositions": []}])
    monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)

    run_pilot(["sample"], _config(), checkpoint_path=checkpoint_path, max_spend_usd=10.0)
    calls_after_first_run = fake_client.calls

    # Second run: same checkpoint file, same doc — must not call the LLM again.
    report = run_pilot(["sample"], _config(), checkpoint_path=checkpoint_path, max_spend_usd=10.0)

    assert fake_client.calls == calls_after_first_run  # no new calls
    assert len(report.documents) == 1


def test_spend_cap_exceeded_raises_before_starting_a_new_document(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        '{"completed": {"already_done": {"doc_name": "already_done", "pages_total": 1, '
        '"pages_skipped": {}, "pages_segmented": 1, "llm_requests": 1, "input_tokens": 1000, '
        '"output_tokens": 200, "propositions": 1, "elapsed_seconds": 1.0, "cost_usd": 5.0}}}',
        encoding="utf-8",
    )
    fake_client = _FakeRoutedLLMClient([{"propositions": []}])
    monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)

    with pytest.raises(SpendCapExceeded):
        run_pilot(["already_done", "sample"], _config(), checkpoint_path=checkpoint_path, max_spend_usd=1.0)


def test_spend_cap_exceeded_raises_mid_document(tmp_path, monkeypatch):
    # 1000 input + 200 output tokens per call ~= $0.0008 at gpt-4.1-mini
    # pricing; a tiny cap forces the cap to trip on the very first call.
    fake_client = _FakeRoutedLLMClient([{"propositions": []}, {"propositions": []}])
    monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)

    with pytest.raises(SpendCapExceeded):
        run_pilot(["sample"], _config(), checkpoint_path=tmp_path / "checkpoint.json", max_spend_usd=0.0001)


def test_pages_skipped_by_reason_is_not_tracked_for_this_fixture(tmp_path, monkeypatch):
    """sample.pdf's two pages are ordinary content (prose + table) — the
    content filter must not skip either."""
    fake_client = _FakeRoutedLLMClient([{"propositions": []}, {"propositions": []}])
    monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)

    report = run_pilot(["sample"], _config(), checkpoint_path=tmp_path / "checkpoint.json", max_spend_usd=10.0)

    assert report.documents[0].pages_skipped == {}
    assert report.documents[0].pages_segmented == 2


def test_a_failed_call_still_counts_its_partial_token_cost(tmp_path, monkeypatch):
    """Regression, 2026-07-25 pilot: a call that raises LLMCallFailed (e.g.
    a length-limit failure) can still have burned real, expensive tokens —
    that cost must be tracked, not silently dropped just because the call
    ultimately failed to produce usable output."""

    class _OnceFailingClient:
        def __init__(self):
            self.last_response_meta = None
            self.calls = 0

        def complete(self, payload):
            self.calls += 1
            if self.calls == 1:
                raise LLMCallFailed(
                    "simulated length-limit failure",
                    partial_response=AdapterResponse(
                        data={}, seed_supported=False, reasoning_tokens=None,
                        input_tokens=5000, output_tokens=32768, adapter_version="fake-1.0",
                    ),
                )
            self.last_response_meta = AdapterResponse(
                data={}, seed_supported=False, reasoning_tokens=None,
                input_tokens=100, output_tokens=50, adapter_version="fake-1.0",
            )
            return {"propositions": []}

    fake_client = _OnceFailingClient()
    monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)

    report = run_pilot(["sample"], _config(), checkpoint_path=tmp_path / "checkpoint.json", max_spend_usd=10.0)

    doc = report.documents[0]
    # Both the failed call's tokens (5000 in / 32768 out) and the retry's
    # successful tokens (100 in / 50 out) must be counted.
    assert doc.input_tokens >= 5000
    assert doc.output_tokens >= 32768
    assert doc.cost_usd > estimate_cost_usd(5000, 32768) - 1e-9


def test_spend_cap_from_a_failed_calls_partial_cost_still_stops_the_run(tmp_path, monkeypatch):
    class _AlwaysFailsWithBigUsage:
        def __init__(self):
            self.last_response_meta = None
            self.calls = 0

        def complete(self, payload):
            self.calls += 1
            raise LLMCallFailed(
                "simulated length-limit failure",
                partial_response=AdapterResponse(
                    data={}, seed_supported=False, reasoning_tokens=None,
                    input_tokens=100_000, output_tokens=100_000, adapter_version="fake-1.0",
                ),
            )

    fake_client = _AlwaysFailsWithBigUsage()
    monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)

    with pytest.raises(SpendCapExceeded):
        run_pilot(["sample"], _config(), checkpoint_path=tmp_path / "checkpoint.json", max_spend_usd=0.05)


class TestSkipRiskFactorsFlag:
    """`skip_risk_factors` is opt-in — off by default so run_pilot's
    behavior for any use outside the metric-axis run is unchanged."""

    def test_default_does_not_call_the_risk_factors_skip(self, tmp_path, monkeypatch):
        fake_client = _FakeRoutedLLMClient([{"propositions": []}, {"propositions": []}])
        monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)
        called = {"n": 0}
        monkeypatch.setattr(
            "offline.pilot.skip_risk_factors_section",
            lambda pages: called.update(n=called["n"] + 1) or (pages, {}),
        )

        run_pilot(["sample"], _config(), checkpoint_path=tmp_path / "checkpoint.json", max_spend_usd=10.0)

        assert called["n"] == 0

    def test_enabling_the_flag_calls_the_risk_factors_skip(self, tmp_path, monkeypatch):
        fake_client = _FakeRoutedLLMClient([{"propositions": []}, {"propositions": []}])
        monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)
        called = {"n": 0}
        monkeypatch.setattr(
            "offline.pilot.skip_risk_factors_section",
            lambda pages: called.update(n=called["n"] + 1) or (pages, {}),
        )

        run_pilot(
            ["sample"], _config(), checkpoint_path=tmp_path / "checkpoint.json",
            max_spend_usd=10.0, skip_risk_factors=True,
        )

        assert called["n"] == 1


def test_infrastructure_error_halts_the_run_without_checkpointing_a_partial_document(tmp_path, monkeypatch):
    """Regression, 2026-07-25: a quota/auth/connection failure mid-document
    must propagate out of run_pilot entirely -- not be caught anywhere in
    the pilot's LLMCallFailed handling -- and the document must NOT be
    checkpointed as completed. A silently-swallowed infrastructure failure
    would otherwise write a checkpoint entry for a document segmented from
    mostly-empty pages, indistinguishable later from a real, complete run."""

    class _InfrastructureFailingClient:
        def __init__(self):
            self.last_response_meta = None
            self.calls = 0

        def complete(self, payload):
            self.calls += 1
            raise LLMInfrastructureError("simulated insufficient_quota failure")

    fake_client = _InfrastructureFailingClient()
    monkeypatch.setattr("offline.pilot.build_llm_client", lambda *a, **k: fake_client)
    checkpoint_path = tmp_path / "checkpoint.json"

    with pytest.raises(LLMInfrastructureError):
        run_pilot(["sample"], _config(), checkpoint_path=checkpoint_path, max_spend_usd=10.0)

    assert not checkpoint_path.exists() or json.loads(checkpoint_path.read_text())["completed"] == {}


def test_estimate_cost_usd_matches_gpt41_mini_pricing():
    cost = estimate_cost_usd(input_tokens=1_000_000, output_tokens=1_000_000)

    assert cost == pytest.approx(0.40 + 1.60)


class TestPilotReport:
    def test_totals_sum_across_documents(self):
        report = PilotReport(
            documents=[
                DocumentPilotResult("a", 5, {}, 5, 3, 1000, 200, 4, 10.0, 0.5),
                DocumentPilotResult("b", 8, {1: "cover"}, 7, 4, 1500, 300, 6, 15.0, 0.8),
            ]
        )

        assert report.total_cost_usd == pytest.approx(1.3)
        assert report.total_requests == 7
        assert report.total_input_tokens == 2500
        assert report.total_output_tokens == 500
        assert report.total_elapsed_seconds == pytest.approx(25.0)
        assert report.pages_skipped_by_reason == {"cover": 1}

    def test_extrapolate_scales_by_corpus_ratio(self):
        report = PilotReport(
            documents=[
                DocumentPilotResult("a", 5, {}, 5, 3, 1000, 200, 4, 10.0, 1.0),
                DocumentPilotResult("b", 5, {}, 5, 3, 1000, 200, 4, 10.0, 1.0),
                DocumentPilotResult("c", 5, {}, 5, 3, 1000, 200, 4, 10.0, 1.0),
            ]
        )

        extrapolated = report.extrapolate(full_corpus_size=84)

        assert extrapolated["estimated_cost_usd"] == pytest.approx(3.0 * (84 / 3))
        assert extrapolated["estimated_requests"] == round(9 * (84 / 3))

    def test_extrapolate_with_no_documents_is_empty(self):
        assert PilotReport().extrapolate(full_corpus_size=84) == {}
