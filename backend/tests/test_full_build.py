"""Full build orchestration tests. Fake LLM client, but real Postgres + FAISS via
clean_postgres, so these exercise the actual persistence path the real build uses.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.schema import ExperimentConfig
from llm.adapters.base import AdapterResponse, LLMInfrastructureError
from llm.routing import LLMCallSiteManifest
from offline.full_build import run_full_build
from offline.indexing.build import load_index
from offline.parsers.layout_parser import ParsedDocument, ParsedPage

_ALL_CALL_SITES = {
    name: {"provider": "openai", "model": "m"}
    for name in (
        "proposition_segmenter", "numeric_qualifier_resolver", "query_splitter",
        "fff_rewriter", "generator", "cove_verification", "step_planner",
    )
}


def _config() -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "experiment_id": "test_full_build",
            "enable_npce": False,
            "enable_cove": False,
            "retrieval": {"mode": "hybrid", "top_k": 20, "rerank_top_k": 5, "enable_reranker": True, "time_weighting": True},
            "indexing": {"granularity": "proposition"},
            "llm": {
                "defaults": {"temperature": 0.0, "structured_output": True},
                "call_sites": _ALL_CALL_SITES,
                "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
            },
            "corpus": {"pdf_dir": "data/pdfs", "required_docs_file": "data/metric_axis_required_docs.txt"},
        }
    )


_COVER_TEXT = (
    "UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nFORM 10-K\n"
    "For the fiscal year ended December 31, 2018\nACME CORP"
)
_BODY_TEXT = (
    "Consolidated Statement of Income. Net sales were $32,765 million in the year ended December 31, 2018.\n"
    "Net sales were $31,657 million in the year ended December 31, 2017."
)
_FAKE_DOC = ParsedDocument(
    pages=[ParsedPage(page_number=0, text=_COVER_TEXT, tables=[]), ParsedPage(page_number=1, text=_BODY_TEXT, tables=[])],
    sections=[],
    source_format="pdf",
)


class _FakeEmbedder:
    model_name = "fake-embedder"
    revision = "fake-rev"
    dimension = 8

    def encode_passages(self, texts):
        return [[float(i % 8) for i in range(8)] for _ in texts]


class _FakeRoutedLLMClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.last_response_meta = None
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        self.last_response_meta = AdapterResponse(
            data={}, seed_supported=False, reasoning_tokens=None,
            input_tokens=100, output_tokens=50, adapter_version="fake-1.0",
        )
        return self._responses.pop(0) if self._responses else {"propositions": []}

    def current_tpm_usage(self):
        return 12345

    def to_manifest(self, call_site_name):
        return LLMCallSiteManifest(
            call_site=call_site_name, provider="openai", model="gpt-4.1-mini-2025-04-14",
            temperature=0.0, seed=None, seed_supported=False, include_reasoning_field=False,
            adapter_version="fake-1.0",
        )


class _AlwaysFailsWithInfrastructureError:
    last_response_meta = None

    def complete(self, payload):
        raise LLMInfrastructureError("simulated quota failure")

    def current_tpm_usage(self):
        return 0

    def to_manifest(self, call_site_name):
        raise AssertionError("to_manifest should never be reached when every call fails")


def _two_propositions_response():
    return {
        "propositions": [
            {"text": "Acme Corp had net sales of $32,765 million in the year ended December 31, 2018.", "source_offset_hint": None},
            {"text": "Acme Corp had net sales of $31,657 million in the year ended December 31, 2017.", "source_offset_hint": None},
        ]
    }


def test_full_build_produces_a_real_three_index_build(clean_postgres, tmp_path, monkeypatch):
    fake_client = _FakeRoutedLLMClient([_two_propositions_response()])
    monkeypatch.setattr("offline.full_build.parse_document", lambda *_: _FAKE_DOC)
    monkeypatch.setattr("offline.full_build.build_llm_client", lambda *a, **k: fake_client)

    manifest = run_full_build(
        doc_names=["ACME_2018_10K"],
        config=_config(),
        pdf_paths={"ACME_2018_10K": Path("fake.pdf")},
        company_names={"ACME_2018_10K": "Acme Corp"},
        checkpoint_path=tmp_path / "checkpoint.json",
        max_spend_usd=10.0,
        embedder=_FakeEmbedder(),
        dense_index_path=tmp_path / "dense_idx",
        postgres_dsn=clean_postgres,
        config_hash="test-hash",
        gold_pages_by_doc={"ACME_2018_10K": {1}},
    )

    assert manifest.config_hash == "test-hash"
    assert manifest.index_build.proposition_count == 2
    assert manifest.index_build.indexed_doc_names == ("ACME_2018_10K",)
    assert manifest.index_build.gold_overlap_clean is True  # gold page 1 was never skipped
    assert manifest.index_build.skipped_pages_by_reason == {"cover": 1}  # page 0
    assert manifest.total_requests == 1
    assert manifest.total_cost_usd > 0
    assert manifest.llm_call_sites[0].call_site == "proposition_segmenter"

    bundle = load_index(tmp_path / "dense_idx", clean_postgres, dimension=8)
    assert bundle.dense_index.ids() == bundle.sparse_index.ids() == bundle.metadata_store.ids()
    assert len(bundle.dense_index.ids()) == 2

    props = bundle.metadata_store.all()
    numeric_values = {p.numeric.value for p in props if p.numeric}
    assert numeric_values == {32765.0, 31657.0}
    assert all(p.metadata.page == 1 for p in props)  # attributed to the body page, not the cover


def test_full_build_gold_overlap_detects_a_skipped_gold_page(clean_postgres, tmp_path, monkeypatch):
    fake_client = _FakeRoutedLLMClient([_two_propositions_response()])
    monkeypatch.setattr("offline.full_build.parse_document", lambda *_: _FAKE_DOC)
    monkeypatch.setattr("offline.full_build.build_llm_client", lambda *a, **k: fake_client)

    manifest = run_full_build(
        doc_names=["ACME_2018_10K"],
        config=_config(),
        pdf_paths={"ACME_2018_10K": Path("fake.pdf")},
        company_names={"ACME_2018_10K": "Acme Corp"},
        checkpoint_path=tmp_path / "checkpoint.json",
        max_spend_usd=10.0,
        embedder=_FakeEmbedder(),
        dense_index_path=tmp_path / "dense_idx",
        postgres_dsn=clean_postgres,
        gold_pages_by_doc={"ACME_2018_10K": {0}},  # page 0 is the (skipped) cover page
    )

    assert manifest.index_build.gold_overlap_clean is False


def test_resuming_from_a_checkpoint_does_not_recall_the_llm(clean_postgres, tmp_path, monkeypatch):
    fake_client = _FakeRoutedLLMClient([_two_propositions_response()])
    monkeypatch.setattr("offline.full_build.parse_document", lambda *_: _FAKE_DOC)
    monkeypatch.setattr("offline.full_build.build_llm_client", lambda *a, **k: fake_client)
    checkpoint_path = tmp_path / "checkpoint.json"

    run_full_build(
        ["ACME_2018_10K"], _config(), {"ACME_2018_10K": Path("fake.pdf")}, {"ACME_2018_10K": "Acme Corp"},
        checkpoint_path, 10.0, _FakeEmbedder(), tmp_path / "idx_a", clean_postgres,
    )
    calls_after_first_run = fake_client.calls

    manifest_second = run_full_build(
        ["ACME_2018_10K"], _config(), {"ACME_2018_10K": Path("fake.pdf")}, {"ACME_2018_10K": "Acme Corp"},
        checkpoint_path, 10.0, _FakeEmbedder(), tmp_path / "idx_b", clean_postgres,
    )

    assert fake_client.calls == calls_after_first_run  # no new LLM calls
    assert manifest_second.index_build.proposition_count == 2


def test_a_run_resuming_every_document_from_checkpoint_still_reports_llm_call_sites(
    clean_postgres, tmp_path, monkeypatch
):
    """Regression: a fully-resumed run (no fresh LLM client built) used to produce an
    empty llm_call_sites. The manifest is now persisted into the checkpoint so it survives resume."""
    fake_client = _FakeRoutedLLMClient([_two_propositions_response()])
    monkeypatch.setattr("offline.full_build.parse_document", lambda *_: _FAKE_DOC)
    monkeypatch.setattr("offline.full_build.build_llm_client", lambda *a, **k: fake_client)
    checkpoint_path = tmp_path / "checkpoint.json"

    run_full_build(
        ["ACME_2018_10K"], _config(), {"ACME_2018_10K": Path("fake.pdf")}, {"ACME_2018_10K": "Acme Corp"},
        checkpoint_path, 10.0, _FakeEmbedder(), tmp_path / "idx_a", clean_postgres,
    )

    # same doc_names again -- everything resumes from checkpoint, no fresh client built
    monkeypatch.setattr(
        "offline.full_build.build_llm_client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not build a fresh client on full resume")),
    )
    manifest = run_full_build(
        ["ACME_2018_10K"], _config(), {"ACME_2018_10K": Path("fake.pdf")}, {"ACME_2018_10K": "Acme Corp"},
        checkpoint_path, 10.0, _FakeEmbedder(), tmp_path / "idx_b", clean_postgres,
    )

    assert len(manifest.llm_call_sites) == 1
    assert manifest.llm_call_sites[0].call_site == "proposition_segmenter"
    assert manifest.llm_call_sites[0].model == "gpt-4.1-mini-2025-04-14"


def test_infrastructure_error_halts_without_writing_a_checkpoint_entry(clean_postgres, tmp_path, monkeypatch):
    """LLMInfrastructureError (quota/auth/connection failure) must stop the run,
    not degrade to an empty document checkpointed as done."""
    monkeypatch.setattr("offline.full_build.parse_document", lambda *_: _FAKE_DOC)
    monkeypatch.setattr(
        "offline.full_build.build_llm_client", lambda *a, **k: _AlwaysFailsWithInfrastructureError()
    )
    checkpoint_path = tmp_path / "checkpoint.json"

    with pytest.raises(LLMInfrastructureError):
        run_full_build(
            ["ACME_2018_10K"], _config(), {"ACME_2018_10K": Path("fake.pdf")}, {"ACME_2018_10K": "Acme Corp"},
            checkpoint_path, 10.0, _FakeEmbedder(), tmp_path / "idx", clean_postgres,
        )

    assert not checkpoint_path.exists()


def test_missing_filing_period_statement_raises_rather_than_fabricating_a_date(clean_postgres, tmp_path, monkeypatch):
    doc_with_no_period_statement = ParsedDocument(
        pages=[ParsedPage(page_number=0, text="No period statement on this cover page.", tables=[])],
        sections=[], source_format="pdf",
    )
    monkeypatch.setattr("offline.full_build.parse_document", lambda *_: doc_with_no_period_statement)
    monkeypatch.setattr("offline.full_build.build_llm_client", lambda *a, **k: _FakeRoutedLLMClient([]))

    with pytest.raises(ValueError, match="filing_date"):
        run_full_build(
            ["ACME_2018_10K"], _config(), {"ACME_2018_10K": Path("fake.pdf")}, {"ACME_2018_10K": "Acme Corp"},
            tmp_path / "checkpoint.json", 10.0, _FakeEmbedder(), tmp_path / "idx", clean_postgres,
        )


def test_escalation_retries_incomplete_batch_with_stronger_model(clean_postgres, tmp_path, monkeypatch):
    """A batch whose table row value gets dropped (segmenter_llm.py: _table_rows_complete)
    retries with the escalation model when proposition_segmenter_escalation is configured."""
    doc = ParsedDocument(
        pages=[
            ParsedPage(page_number=0, text=_COVER_TEXT, tables=[]),
            ParsedPage(
                page_number=1,
                text="Accounts receivable - net of allowances of $95 million   5,020   December 31, 2018",
                tables=[(("Accounts receivable - net of allowances of $95 million", "5,020"),)],
            ),
        ],
        sections=[], source_format="pdf",
    )
    incomplete_response = {
        "propositions": [
            {"text": "Acme Corp had accounts receivable - net of allowances of $95 million.", "source_offset_hint": None},
        ]
    }
    complete_response = {
        "propositions": [
            {"text": "Acme Corp had accounts receivable of $5,020 million at December 31, 2018.", "source_offset_hint": None},
        ]
    }
    primary_client = _FakeRoutedLLMClient([incomplete_response, incomplete_response])  # 2nd response covers the retry
    escalation_client = _FakeRoutedLLMClient([complete_response])

    def _fake_build_llm_client(call_site_name, config, schema_key=None):
        return escalation_client if call_site_name == "proposition_segmenter_escalation" else primary_client

    monkeypatch.setattr("offline.full_build.parse_document", lambda *_: doc)
    monkeypatch.setattr("offline.full_build.build_llm_client", _fake_build_llm_client)

    config_dict = _config().model_dump()
    config_dict["llm"]["call_sites"]["proposition_segmenter_escalation"] = {"provider": "openai", "model": "gpt-5.6-luna"}
    config_with_escalation = ExperimentConfig.model_validate(config_dict)

    manifest = run_full_build(
        doc_names=["ACME_2018_10K"],
        config=config_with_escalation,
        pdf_paths={"ACME_2018_10K": Path("fake.pdf")},
        company_names={"ACME_2018_10K": "Acme Corp"},
        checkpoint_path=tmp_path / "checkpoint.json",
        max_spend_usd=10.0,
        embedder=_FakeEmbedder(),
        dense_index_path=tmp_path / "dense_idx",
        postgres_dsn=clean_postgres,
    )

    assert primary_client.calls == 1  # not retried against itself, escalated instead
    assert escalation_client.calls == 1
    assert manifest.index_build.proposition_count == 1
    assert manifest.total_cost_usd > 0

    bundle = load_index(tmp_path / "dense_idx", clean_postgres, dimension=8)
    props = bundle.metadata_store.all()
    assert props[0].text == "Acme Corp had accounts receivable of $5,020 million at December 31, 2018."


def test_mid_document_crash_resumes_only_the_unfinished_batch(clean_postgres, tmp_path, monkeypatch):
    """A document with 2 batches: first succeeds, second raises an infrastructure error.
    Resuming must not re-send the first batch, only the second."""
    doc = ParsedDocument(
        pages=[
            ParsedPage(page_number=0, text=_COVER_TEXT, tables=[]),
            ParsedPage(page_number=1, text="Acme Corp fact one about FY2024.", tables=[]),
            ParsedPage(page_number=2, text="Acme Corp fact two about FY2024.", tables=[]),
            ParsedPage(page_number=3, text="Acme Corp fact three about FY2024.", tables=[]),
            ParsedPage(page_number=4, text="Acme Corp fact four about FY2024.", tables=[]),
        ],
        sections=[], source_format="pdf",
    )

    class _SucceedsThenInfrastructureFails:
        """Batch 1 (pages 1-2) succeeds; batch 2 (pages 3-4) raises LLMInfrastructureError,
        simulating a real APIConnectionError."""

        def __init__(self):
            self.calls = 0
            self.last_response_meta = None

        def complete(self, payload):
            self.calls += 1
            if self.calls == 1:
                self.last_response_meta = AdapterResponse(
                    data={}, seed_supported=False, reasoning_tokens=None,
                    input_tokens=100, output_tokens=50, adapter_version="fake-1.0",
                )
                return {"propositions": [
                    {"text": "Acme Corp fact one about FY2024.", "source_offset_hint": None},
                    {"text": "Acme Corp fact two about FY2024.", "source_offset_hint": None},
                ]}
            raise LLMInfrastructureError("simulated connection error")

        def current_tpm_usage(self):
            return 0

        def to_manifest(self, call_site_name):
            return LLMCallSiteManifest(
                call_site=call_site_name, provider="openai", model="gpt-4.1-mini-2025-04-14",
                temperature=0.0, seed=None, seed_supported=False, include_reasoning_field=False,
                adapter_version="fake-1.0",
            )

    crashing_client = _SucceedsThenInfrastructureFails()
    monkeypatch.setattr("offline.full_build.parse_document", lambda *_: doc)
    monkeypatch.setattr("offline.full_build.build_llm_client", lambda *a, **k: crashing_client)

    checkpoint_path = tmp_path / "checkpoint.json"
    with pytest.raises(LLMInfrastructureError):
        run_full_build(
            ["ACME_2018_10K"], _config(), {"ACME_2018_10K": Path("fake.pdf")}, {"ACME_2018_10K": "Acme Corp"},
            checkpoint_path, 10.0, _FakeEmbedder(), tmp_path / "idx", clean_postgres,
        )

    assert crashing_client.calls == 2  # batch 1 succeeded, batch 2 crashed, never a 3rd call
    batch_sidecar = checkpoint_path.with_name(f"{checkpoint_path.stem}.ACME_2018_10K.batches.json")
    assert batch_sidecar.exists()
    saved_batches = json.loads(batch_sidecar.read_text(encoding="utf-8"))
    assert list(saved_batches.keys()) == ["0"]  # only batch 0 (pages 1-2) completed before the crash

    # fresh client only has one response queued (batch 2); if batch 1 were
    # re-sent it'd fall through to an empty result instead.
    resuming_client = _FakeRoutedLLMClient(
        [{"propositions": [
            {"text": "Acme Corp fact three about FY2024.", "source_offset_hint": None},
            {"text": "Acme Corp fact four about FY2024.", "source_offset_hint": None},
        ]}]
    )
    monkeypatch.setattr("offline.full_build.build_llm_client", lambda *a, **k: resuming_client)

    manifest = run_full_build(
        ["ACME_2018_10K"], _config(), {"ACME_2018_10K": Path("fake.pdf")}, {"ACME_2018_10K": "Acme Corp"},
        checkpoint_path, 10.0, _FakeEmbedder(), tmp_path / "idx", clean_postgres,
    )

    assert resuming_client.calls == 1  # only the unfinished batch was re-sent
    assert manifest.index_build.proposition_count == 4  # all 4 facts present, from both attempts combined
    assert not batch_sidecar.exists()  # cleaned up once the document completed

    bundle = load_index(tmp_path / "idx", clean_postgres, dimension=8)
    texts = {p.text for p in bundle.metadata_store.all()}
    assert texts == {
        "Acme Corp fact one about FY2024.",
        "Acme Corp fact two about FY2024.",
        "Acme Corp fact three about FY2024.",
        "Acme Corp fact four about FY2024.",
    }
