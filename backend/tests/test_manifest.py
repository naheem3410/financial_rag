"""Run manifest tests."""
from __future__ import annotations

from llm.routing import LLMCallSiteManifest
from offline.indexing.build import BuildManifest
from offline.manifest import RunManifest, compute_config_hash


def test_compute_config_hash_is_deterministic():
    config = {"experiment_id": "c1_baseline", "enable_npce": False}

    assert compute_config_hash(config) == compute_config_hash(dict(config))


def test_compute_config_hash_differs_for_different_config():
    a = compute_config_hash({"experiment_id": "c1_baseline"})
    b = compute_config_hash({"experiment_id": "c4_full"})

    assert a != b


def test_compute_config_hash_is_key_order_independent():
    a = compute_config_hash({"x": 1, "y": 2})
    b = compute_config_hash({"y": 2, "x": 1})

    assert a == b


def test_run_manifest_exposes_indexed_doc_names_from_index_build():
    index_build = BuildManifest(
        build_id="abc123", proposition_count=10, embedder_model_name="m", embedder_revision="r",
        embedder_dimension=768, indexed_doc_names=("3M_2018_10K", "ADOBE_2015_10K"),
    )
    manifest = RunManifest(config_hash="cfg", index_build=index_build, llm_call_sites=())

    assert manifest.indexed_doc_names == ("3M_2018_10K", "ADOBE_2015_10K")


def test_run_manifest_records_multiple_call_sites_with_different_providers():
    index_build = BuildManifest(
        build_id="abc123", proposition_count=10, embedder_model_name="m", embedder_revision="r",
        embedder_dimension=768,
    )
    call_sites = (
        LLMCallSiteManifest(
            call_site="proposition_segmenter", provider="openai", model="gpt-4.1-mini-2025-04-14",
            temperature=0.0, seed=42, seed_supported=False, include_reasoning_field=False,
            adapter_version="1.0.0",
        ),
        LLMCallSiteManifest(
            call_site="cove_verification", provider="anthropic", model="claude-x-2025-01-01",
            temperature=0.0, seed=None, seed_supported=False, include_reasoning_field=True,
            adapter_version="1.0.0",
        ),
    )
    manifest = RunManifest(config_hash="cfg", index_build=index_build, llm_call_sites=call_sites)

    providers = {cs.provider for cs in manifest.llm_call_sites}
    assert providers == {"openai", "anthropic"}


def test_run_manifest_to_dict_is_json_serializable():
    import json

    index_build = BuildManifest(
        build_id="abc123", proposition_count=1, embedder_model_name="m", embedder_revision="r",
        embedder_dimension=768, indexed_doc_names=("3M_2018_10K",),
    )
    call_sites = (
        LLMCallSiteManifest(
            call_site="proposition_segmenter", provider="openai", model="gpt-4.1-mini-2025-04-14",
            temperature=0.0, seed=42, seed_supported=False, include_reasoning_field=False,
            adapter_version="1.0.0",
        ),
    )
    manifest = RunManifest(config_hash="cfg", index_build=index_build, llm_call_sites=call_sites)

    serialized = json.dumps(manifest.to_dict())

    assert "proposition_segmenter" in serialized
    assert "3M_2018_10K" in serialized


def test_run_manifest_records_run_execution_stats():
    index_build = BuildManifest(
        build_id="abc123", proposition_count=100, embedder_model_name="m", embedder_revision="r",
        embedder_dimension=768,
    )
    manifest = RunManifest(
        config_hash="cfg", index_build=index_build, llm_call_sites=(),
        total_requests=213, total_input_tokens=432540, total_output_tokens=1268679,
        total_cost_usd=2.2029, effective_concurrency=3.72,
    )

    assert manifest.total_requests == 213
    assert manifest.total_cost_usd == 2.2029
    assert manifest.effective_concurrency == 3.72
    assert manifest.to_dict()["total_input_tokens"] == 432540


def test_run_manifest_stats_default_to_none():
    index_build = BuildManifest(
        build_id="abc123", proposition_count=0, embedder_model_name="m", embedder_revision="r",
        embedder_dimension=768,
    )
    manifest = RunManifest(config_hash="cfg", index_build=index_build, llm_call_sites=())

    assert manifest.total_requests is None
    assert manifest.to_dict()["total_cost_usd"] is None


def test_build_manifest_records_skipped_pages_and_gold_overlap_result():
    manifest = BuildManifest(
        build_id="abc123", proposition_count=10, embedder_model_name="m", embedder_revision="r",
        embedder_dimension=768, skipped_pages_by_reason={"risk_factors_item_1a": 30, "cover": 4},
        gold_overlap_clean=True,
    )

    assert manifest.skipped_pages_by_reason == {"risk_factors_item_1a": 30, "cover": 4}
    assert manifest.gold_overlap_clean is True


def test_build_manifest_skipped_pages_and_gold_overlap_default_empty_and_none():
    manifest = BuildManifest(
        build_id="abc123", proposition_count=0, embedder_model_name="m", embedder_revision="r",
        embedder_dimension=768,
    )

    assert manifest.skipped_pages_by_reason == {}
    assert manifest.gold_overlap_clean is None
