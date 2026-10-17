"""Config schema tests: the never-disable guardrails, shared-retrieval-core
check, embedding pin validation, and per-call-site LLM routing."""
from __future__ import annotations

import glob

import pytest
from pydantic import ValidationError

from config.schema import CALL_SITE_NAMES, OPTIONAL_CALL_SITE_NAMES, ExperimentConfig, load_config

_ALL_CALL_SITES = {
    name: {"provider": "openai", "model": f"{name}-model-2025-01-01"} for name in CALL_SITE_NAMES
}

_BASE = dict(
    experiment_id="c1_baseline",
    enable_npce=False,
    enable_cove=False,
    retrieval=dict(mode="hybrid", top_k=20, rerank_top_k=5, enable_reranker=True, time_weighting=True),
    indexing=dict(granularity="proposition"),
    llm=dict(
        defaults={"temperature": 0.0, "structured_output": True},
        call_sites=_ALL_CALL_SITES,
        providers={"openai": {"api_key_env": "OPENAI_API_KEY"}},
    ),
)


def test_all_five_experiment_yamls_load_and_validate():
    paths = sorted(glob.glob("config/experiments/*.yaml"))
    assert len(paths) == 5
    for path in paths:
        cfg = load_config(path)
        assert cfg.embedding.dimension == 768
        assert cfg.corpus.pdf_dir  # read from config, not hardcoded
        # exactly the seven required call sites (step_planner included,
        # step 4.6) plus the documented optional exceptions; anything more
        # is an undocumented extra
        keys = set(cfg.llm.call_sites.keys())
        assert set(CALL_SITE_NAMES) <= keys <= set(CALL_SITE_NAMES) | set(OPTIONAL_CALL_SITE_NAMES)


def test_plain_ai_baseline_config_loads_and_validates():
    """Step 5.1, owner's yes given 2026-10-10. Lives in config/baselines/,
    not config/experiments/ -- it isn't one of the five C0-C4 cells, so it
    must never count toward (or be found by) the "exactly five" check above."""
    cfg = load_config("config/baselines/plain_ai_baseline.yaml")

    assert cfg.experiment_id == "plain_ai_baseline"
    assert "plain_ai_baseline" in cfg.llm.call_sites
    assert cfg.llm.call_sites["plain_ai_baseline"].model == "gpt-4.1-mini-2025-04-14"


def test_corpus_path_is_configurable_not_hardcoded():
    cfg = ExperimentConfig.model_validate(
        {**_BASE, "corpus": {"pdf_dir": "somewhere/else", "required_docs_file": "somewhere/else/allowlist.txt"}}
    )

    assert cfg.corpus.pdf_dir == "somewhere/else"
    assert cfg.corpus.required_docs_file == "somewhere/else/allowlist.txt"


def test_corpus_defaults_match_where_the_user_placed_the_data():
    cfg = ExperimentConfig.model_validate(_BASE)

    assert cfg.corpus.pdf_dir == "data/pdfs"
    assert cfg.corpus.required_docs_file == "data/required_docs.txt"


def test_numeric_verbatim_guard_cannot_be_disabled():
    with pytest.raises(ValidationError, match="numeric_verbatim"):
        ExperimentConfig.model_validate(
            {**_BASE, "guards": {"numeric_verbatim": False, "deterministic_numeric_comparison": True}}
        )


def test_deterministic_numeric_comparison_guard_cannot_be_disabled():
    with pytest.raises(ValidationError, match="deterministic_numeric_comparison"):
        ExperimentConfig.model_validate(
            {**_BASE, "guards": {"numeric_verbatim": True, "deterministic_numeric_comparison": False}}
        )


def test_naive_retrieval_settings_rejected_outside_c0():
    bad = {**_BASE, "retrieval": {**_BASE["retrieval"], "mode": "naive_dense"}}

    with pytest.raises(ValidationError, match="c0_naive"):
        ExperimentConfig.model_validate(bad)


def test_naive_retrieval_settings_accepted_for_c0():
    cfg = {
        **_BASE,
        "experiment_id": "c0_naive",
        "retrieval": {**_BASE["retrieval"], "mode": "naive_dense", "enable_reranker": False},
        "indexing": {"granularity": "fixed_size"},
    }

    validated = ExperimentConfig.model_validate(cfg)

    assert validated.experiment_id == "c0_naive"


def test_nonzero_defaults_temperature_rejected():
    bad = {**_BASE, "llm": {**_BASE["llm"], "defaults": {"temperature": 0.7, "structured_output": True}}}

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(bad)


def test_nonzero_call_site_temperature_rejected():
    call_sites = {**_ALL_CALL_SITES, "generator": {"provider": "openai", "model": "gen-2025-01-01", "temperature": 0.5}}
    bad = {**_BASE, "llm": {**_BASE["llm"], "call_sites": call_sites}}

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(bad)


def test_embedding_revision_defaults_to_pinned_hash():
    cfg = ExperimentConfig.model_validate(_BASE)

    assert cfg.embedding.model == "BAAI/bge-base-en-v1.5"
    assert cfg.embedding.dimension == 768
    assert cfg.embedding.revision != "main"


def test_embedding_revision_rejects_mutable_ref():
    bad = {**_BASE, "embedding": {"model": "BAAI/bge-base-en-v1.5", "revision": "main", "dimension": 768}}

    with pytest.raises(ValidationError, match="pinned commit hash"):
        ExperimentConfig.model_validate(bad)


def test_embedding_dimension_must_be_positive():
    bad = {
        **_BASE,
        "embedding": {"model": "BAAI/bge-base-en-v1.5", "revision": "abc123", "dimension": 0},
    }

    with pytest.raises(ValidationError, match="positive"):
        ExperimentConfig.model_validate(bad)


class TestLLMRoutingConfig:
    def test_all_six_call_sites_required(self):
        incomplete = {name: cfg for name, cfg in _ALL_CALL_SITES.items() if name != "generator"}
        bad = {**_BASE, "llm": {**_BASE["llm"], "call_sites": incomplete}}

        with pytest.raises(ValidationError, match="generator"):
            ExperimentConfig.model_validate(bad)

    def test_call_site_provider_must_be_registered(self):
        call_sites = {**_ALL_CALL_SITES, "generator": {"provider": "anthropic", "model": "claude-x-2025-01-01"}}
        bad = {**_BASE, "llm": {**_BASE["llm"], "call_sites": call_sites}}  # no "anthropic" in providers

        with pytest.raises(ValidationError, match="anthropic"):
            ExperimentConfig.model_validate(bad)

    def test_two_call_sites_can_use_two_different_providers(self):
        call_sites = {
            **_ALL_CALL_SITES,
            "cove_verification": {"provider": "anthropic", "model": "claude-x-2025-01-01"},
        }
        cfg = ExperimentConfig.model_validate(
            {
                **_BASE,
                "llm": {
                    **_BASE["llm"],
                    "call_sites": call_sites,
                    "providers": {**_BASE["llm"]["providers"], "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"}},
                },
            }
        )

        assert cfg.llm.call_sites["proposition_segmenter"].provider == "openai"
        assert cfg.llm.call_sites["cove_verification"].provider == "anthropic"

    def test_floating_alias_model_is_rejected(self):
        call_sites = {**_ALL_CALL_SITES, "generator": {"provider": "openai", "model": "latest"}}
        bad = {**_BASE, "llm": {**_BASE["llm"], "call_sites": call_sites}}

        with pytest.raises(ValidationError, match="pinned snapshot"):
            ExperimentConfig.model_validate(bad)

    def test_call_site_concurrency_must_be_positive(self):
        call_sites = {
            **_ALL_CALL_SITES,
            "proposition_segmenter": {"provider": "openai", "model": "seg-2025-01-01", "concurrency": 0},
        }
        bad = {**_BASE, "llm": {**_BASE["llm"], "call_sites": call_sites}}

        with pytest.raises(ValidationError, match="positive"):
            ExperimentConfig.model_validate(bad)

    def test_include_reasoning_field_defaults_true_except_where_overridden(self):
        call_sites = {
            **_ALL_CALL_SITES,
            "proposition_segmenter": {
                "provider": "openai", "model": "gpt-4.1-mini-2025-04-14",
                "concurrency": 16, "include_reasoning_field": False,
            },
        }
        cfg = ExperimentConfig.model_validate({**_BASE, "llm": {**_BASE["llm"], "call_sites": call_sites}})

        assert cfg.llm.call_sites["proposition_segmenter"].include_reasoning_field is False
        assert cfg.llm.call_sites["generator"].include_reasoning_field is True
