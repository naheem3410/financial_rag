"""run_pilot_comparison.py tests -- no real API calls, no real index. Fake
clients stand in for the LLM boundary (same pattern test_run_plain_ai_gate.py
and test_run_c2_gate.py already use: a small object with `.complete()` and
`.last_response_meta`, injected in place of `build_llm_client`'s real
adapter), and fake bundle/embedder stand in for C0's index for the same
reason -- this only validates the wiring and the checkpoint/resume
behaviour, never a real pipeline run against real data.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import evaluation.run_c0_gate as run_c0_gate_module
import evaluation.run_plain_ai_gate as run_plain_ai_gate_module
import evaluation.run_pilot_comparison as rpc
from contracts.models import Confidence, Proposition, PropositionMetadata

GOLD_INDEX = {
    "c_1": {
        "collision_id": "c_1", "question": "What was Acme's net income in FY2020?",
        "verification_status": "verified", "gold_answers": {"net_income": 5777}, "scale": "millions",
        "collision_axis": "temporal",
    },
    "c_2": {
        "collision_id": "c_2", "question": "What was Acme's net income in FY2021?",
        "verification_status": "verified", "gold_answers": {"net_income": 999}, "scale": "millions",
        "collision_axis": "metric",
    },
}


class _FakeResponseMeta:
    def __init__(self, input_tokens=10, output_tokens=5):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeLLMClient:
    """Stands in for build_llm_client's real RoutedLLMClient -- `.complete()`
    returns a fixed, schema-shaped response, `.last_response_meta` is read by
    `_tracked_client` right after. Counts calls so a resumed run can prove it
    never touched this client again."""

    def __init__(self, response: dict):
        self._response = response
        self.last_response_meta = None
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        self.last_response_meta = _FakeResponseMeta()
        return self._response


def _plain_ai_response(metric="net_income", value="$5,777 million", unit="millions"):
    return {
        "reasoning": "r", "answerable": True, "answer_text": "a",
        "answers": [{"metric": metric, "answer_value": value, "answer_unit": unit}],
    }


# --- load_item_ids ---


def test_load_item_ids_accepts_pilot_20_shape(tmp_path):
    path = tmp_path / "items.json"
    path.write_text(json.dumps({"items": [{"collision_id": "c_1"}, {"collision_id": "c_2"}]}), encoding="utf-8")

    assert rpc.load_item_ids(path) == ["c_1", "c_2"]


def test_load_item_ids_accepts_plain_list(tmp_path):
    path = tmp_path / "items.json"
    path.write_text(json.dumps(["c_1", "c_2"]), encoding="utf-8")

    assert rpc.load_item_ids(path) == ["c_1", "c_2"]


# --- build_records ---


def test_build_records_merges_gold_fields():
    records = rpc.build_records(["c_1"], GOLD_INDEX)

    assert records == [
        {
            "collision_id": "c_1", "question": "What was Acme's net income in FY2020?",
            "verification_status": "verified", "gold_answers": {"net_income": 5777}, "scale": "millions",
            "collision_axis": "temporal",
        }
    ]


def test_build_records_raises_on_unknown_id():
    with pytest.raises(KeyError):
        rpc.build_records(["does_not_exist"], GOLD_INDEX)


# --- plain_ai setup: full real run_plain_ai_gate, fake client only ---


def test_run_setup_plain_ai_scores_and_checkpoints(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "plain_ai_checkpoint.json"
    fake_client = _FakeLLMClient(_plain_ai_response())
    monkeypatch.setattr(run_plain_ai_gate_module, "build_llm_client", lambda *a, **k: fake_client)
    monkeypatch.setattr(rpc, "build_plain_ai_context", lambda: {"config": _FakeConfigWithCallSite()})

    records = rpc.build_records(["c_1"], GOLD_INDEX)
    results = rpc.run_setup("plain_ai", records, checkpoint_path, max_spend_usd=100.0)

    assert len(results) == 1
    assert results[0].collision_id == "c_1"
    assert results[0].exact_match is True
    assert fake_client.calls == 1
    assert checkpoint_path.exists()
    saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert "c_1" in saved["completed"]


def test_run_setup_plain_ai_resumes_without_calling_the_client_again(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "plain_ai_checkpoint.json"
    fake_client = _FakeLLMClient(_plain_ai_response())
    monkeypatch.setattr(run_plain_ai_gate_module, "build_llm_client", lambda *a, **k: fake_client)
    monkeypatch.setattr(rpc, "build_plain_ai_context", lambda: {"config": _FakeConfigWithCallSite()})
    records = rpc.build_records(["c_1"], GOLD_INDEX)

    rpc.run_setup("plain_ai", records, checkpoint_path, max_spend_usd=100.0)
    assert fake_client.calls == 1

    results_again = rpc.run_setup("plain_ai", records, checkpoint_path, max_spend_usd=100.0)
    assert fake_client.calls == 1  # unchanged -- served straight from checkpoint
    assert results_again[0].collision_id == "c_1"


class _FakeConfigWithCallSite:
    """Only needs `llm.call_sites["plain_ai_baseline"].model` -- the one
    attribute `run_plain_ai_question_once` reads off the config before
    `build_llm_client` is called (and that's been monkeypatched away here)."""

    class _LLM:
        call_sites = {"plain_ai_baseline": SimpleNamespace(model="fake-model")}

    llm = _LLM()


# --- c0 setup: fake bundle/embedder context, fake client ---


def _fake_chunk(chunk_id="c_1_chunk_1", text="Acme's net income in FY2020 was $5,777 million."):
    return Proposition(
        proposition_id=chunk_id, text=text, provenance_tier="retrieved", ingestion_path="A_unstructured",
        numeric=None, confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="Acme", filing_type="10-K", filing_date="2020", section="", page=1, source_uri="u"),
    )


class _FakeDenseIndex:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query_vector, k):
        return self._hits[:k]


class _FakeMetadataStore:
    def __init__(self, chunks_by_id):
        self._chunks = chunks_by_id

    def get(self, chunk_id):
        return self._chunks.get(chunk_id)


class _FakeBundle:
    def __init__(self, chunk):
        self.dense_index = _FakeDenseIndex([(chunk.proposition_id, 0.9)])
        self.metadata_store = _FakeMetadataStore({chunk.proposition_id: chunk})


class _FakeEmbedder:
    def encode_queries(self, texts):
        return [[0.0] * 4 for _ in texts]


class _FakeC0Config:
    class _Retrieval:
        top_k = 5
        rerank_top_k = 5

    class _LLM:
        call_sites = {"generator": SimpleNamespace(model="fake-model")}

    retrieval = _Retrieval()
    llm = _LLM()
    experiment_id = "c0_naive_fake"


def test_run_setup_c0_scores_and_checkpoints(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "c0_checkpoint.json"
    chunk = _fake_chunk()
    fake_client = _FakeLLMClient(_plain_ai_response())  # same schema shape the naive generator reads
    monkeypatch.setattr(run_c0_gate_module, "build_llm_client", lambda *a, **k: fake_client)
    monkeypatch.setattr(
        rpc, "build_c0_context",
        lambda: {"config": _FakeC0Config(), "embedder": _FakeEmbedder(), "bundle": _FakeBundle(chunk)},
    )

    records = rpc.build_records(["c_1"], GOLD_INDEX)
    results = rpc.run_setup("c0", records, checkpoint_path, max_spend_usd=100.0)

    assert len(results) == 1
    assert results[0].exact_match is True
    assert checkpoint_path.exists()

    results_again = rpc.run_setup("c0", records, checkpoint_path, max_spend_usd=100.0)
    assert fake_client.calls == 1  # second run served from checkpoint
    assert results_again[0].collision_id == "c_1"


# --- c4_plus setup: dispatch wiring only (6 LLM call sites is too heavy to fake end to end here) ---


def test_run_setup_c4_plus_dispatches_with_context_and_checkpoint(tmp_path, monkeypatch):
    captured = {}

    def _fake_run_c4_gate(records, bundle, embedder, reranker, config, registry, checkpoint_path, max_spend_usd):
        captured.update(
            records=records, bundle=bundle, embedder=embedder, reranker=reranker,
            config=config, registry=registry, checkpoint_path=checkpoint_path, max_spend_usd=max_spend_usd,
        )
        return ["fake-result"]

    fake_ctx = {"config": "cfg", "embedder": "emb", "bundle": "bun", "reranker": "rr", "registry": "reg"}
    monkeypatch.setattr(rpc, "build_c4_plus_context", lambda: fake_ctx)
    monkeypatch.setattr(rpc, "run_c4_gate", _fake_run_c4_gate)

    records = rpc.build_records(["c_2"], GOLD_INDEX)
    checkpoint_path = tmp_path / "c4_plus_checkpoint.json"
    results = rpc.run_setup("c4_plus", records, checkpoint_path, max_spend_usd=42.0)

    assert results == ["fake-result"]
    assert captured["records"] == records
    assert captured["bundle"] == "bun"
    assert captured["embedder"] == "emb"
    assert captured["reranker"] == "rr"
    assert captured["config"] == "cfg"
    assert captured["registry"] == "reg"
    assert captured["checkpoint_path"] == checkpoint_path
    assert captured["max_spend_usd"] == 42.0


def test_run_setup_rejects_unknown_setup(tmp_path):
    with pytest.raises(ValueError):
        rpc.run_setup("not_a_real_setup", [], tmp_path / "x.json", 1.0)


# --- main() CLI, plain_ai end to end ---


def test_main_cli_runs_plain_ai_end_to_end(tmp_path, monkeypatch):
    items_path = tmp_path / "items.json"
    items_path.write_text(json.dumps(["c_1"]), encoding="utf-8")
    checkpoint_path = tmp_path / "checkpoint.json"

    fake_client = _FakeLLMClient(_plain_ai_response())
    monkeypatch.setattr(run_plain_ai_gate_module, "build_llm_client", lambda *a, **k: fake_client)
    monkeypatch.setattr(rpc, "build_plain_ai_context", lambda: {"config": _FakeConfigWithCallSite()})
    monkeypatch.setattr(rpc, "load_gold_index", lambda: GOLD_INDEX)

    rpc.main([
        "--setup", "plain_ai",
        "--items", str(items_path),
        "--checkpoint", str(checkpoint_path),
        "--max-spend-usd", "10.0",
    ])

    assert checkpoint_path.exists()
    saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert "c_1" in saved["completed"]
