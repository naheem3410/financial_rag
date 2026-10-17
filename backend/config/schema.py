"""Typed experiment config for the five cells (C0-C4).
enable_npce/enable_cove pick the QueryTransformer/Verifier impl; everything else must match across C1-C4 (see _shared_retrieval_core)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RetrievalMode = Literal["hybrid", "naive_dense"]
IndexGranularity = Literal["proposition", "fixed_size"]
Provider = Literal["openai", "anthropic", "ollama"]

# Seven call sites total (step 4.6, owner's yes given 2026-09-26: the
# project's own written rule of six is changed, in writing, to seven for
# step_planner, the step-by-step road -- no other job can plan a question
# where one piece needs another piece's answer first). CoVe's 3 sub-calls
# still collapse into one config entry.
CALL_SITE_NAMES: tuple[str, ...] = (
    "proposition_segmenter",
    "numeric_qualifier_resolver",
    "query_splitter",
    "fff_rewriter",
    "generator",
    "cove_verification",
    "step_planner",
)

# Not a required call site — same task as proposition_segmenter on a fallback model,
# triggered only by the table-row completeness check. Optional; omitting it means no escalation.
# metric_label_fallback (step 1.4, owner's yes given 2026-09-26): labels a fact's metric only
# when the rule-based labeler (offline/labeling/metric_labeler.py) found nothing at all --
# most facts never reach it. Optional; omitting it means those facts simply stay unlabelled.
# antecedent_extraction_fallback (owner's yes given 2026-09-26): finds who/when a policy rule
# block applies to, since rules alone can't reliably tell "who this applies to" apart from
# "what it requires". Optional; omitting it means a policy fact's antecedent stays None.
# paper_split_fallback (owner's yes given 2026-09-26): finds look-alike phrases a question
# compares when the free rule-based "X vs Y" check finds nothing -- step 4.3. Optional;
# omitting it means only the plain, explicit comparison shape is ever split.
# plain_ai_baseline (step 5.1, decision 1, owner's yes given 2026-10-10): the model answers a
# question with no retrieval at all, the floor every other setup is compared against. Optional
# and only ever configured for the dedicated baseline config, never for C0-C4.
OPTIONAL_CALL_SITE_NAMES: tuple[str, ...] = (
    "proposition_segmenter_escalation", "metric_label_fallback", "antecedent_extraction_fallback",
    "paper_split_fallback", "plain_ai_baseline",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalConfig(_Strict):
    mode: RetrievalMode = "hybrid"
    top_k: int = 20
    rerank_top_k: int = 5
    enable_reranker: bool = True
    time_weighting: bool = True
    # Step 1.4: filter candidates by the sub-query's metric label, not just
    # company/year. Off by default so every existing config keeps behaving
    # exactly as before; only the new C4+ setup (5.2) turns it on.
    enable_metric_filter: bool = False


class IndexingConfig(_Strict):
    granularity: IndexGranularity = "proposition"


class LLMDefaults(_Strict):
    temperature: float = 0.0
    structured_output: bool = True

    @field_validator("temperature")
    @classmethod
    def _zero_temperature(cls, v: float) -> float:
        # Every LLM call runs at temperature=0, fixed seed, pinned model version.
        if v != 0.0:
            raise ValueError("llm.defaults.temperature must be 0.0")
        return v


class CallSiteLLMConfig(_Strict):
    """One of the six call sites; provider/model swap via YAML, no code change.
    include_reasoning_field defaults True, except the segmenter, which is pure instruction-following and gets no benefit from it."""

    provider: Provider
    model: str
    temperature: float = 0.0
    seed: Optional[int] = None
    concurrency: int = 1
    include_reasoning_field: bool = True

    @field_validator("temperature")
    @classmethod
    def _zero_temperature(cls, v: float) -> float:
        if v != 0.0:
            raise ValueError("call-site temperature must be 0.0")
        return v

    @field_validator("concurrency")
    @classmethod
    def _concurrency_is_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("call-site concurrency must be positive")
        return v

    @field_validator("model")
    @classmethod
    def _model_is_not_a_floating_alias(cls, v: str) -> str:
        # Only catches obvious cases ("latest", empty) — not exhaustive per-provider knowledge.
        # Caller is responsible for supplying an actual pinned snapshot.
        if v.strip().lower() in {"latest", ""}:
            raise ValueError(f"call-site model must be a pinned snapshot, not {v!r}")
        return v


class ProviderConnectionConfig(_Strict):
    """Per-provider connection and rate-limit settings.
    Only a local provider (Ollama) actually guarantees determinism from a seed; hosted APIs don't."""

    api_key_env: Optional[str] = None
    base_url: Optional[str] = None
    rpm: Optional[int] = None
    tpm: Optional[int] = None
    rpd: Optional[int] = None
    seed: Optional[int] = None


class LLMConfig(_Strict):
    defaults: LLMDefaults = Field(default_factory=LLMDefaults)
    call_sites: dict[str, CallSiteLLMConfig] = Field(default_factory=dict)
    providers: dict[str, ProviderConnectionConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _all_six_call_sites_configured(self) -> "LLMConfig":
        missing = set(CALL_SITE_NAMES) - set(self.call_sites.keys())
        if missing:
            raise ValueError(
                f"llm.call_sites is missing required call site(s) {sorted(missing)} "
                "-- exactly seven call sites, all must be configured"
            )
        return self

    @model_validator(mode="after")
    def _every_call_site_provider_is_registered(self) -> "LLMConfig":
        for name, call_site in self.call_sites.items():
            if call_site.provider not in self.providers:
                raise ValueError(
                    f"llm.call_sites.{name} uses provider {call_site.provider!r}, "
                    f"which has no llm.providers.{call_site.provider} entry"
                )
        return self


class EmbeddingConfig(_Strict):
    """Shared embedder identity, recorded in the run manifest.
    An unpinned embedding checkpoint breaks reproducibility the same way an unpinned LLM would."""

    model: str = "BAAI/bge-base-en-v1.5"
    revision: str = "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"
    dimension: int = 768

    @field_validator("revision")
    @classmethod
    def _revision_is_pinned(cls, v: str) -> str:
        # HuggingFace checkpoints can be updated in place at a fixed model
        # name — a branch name like "main" is not a pin.
        if v.strip().lower() in {"main", "latest", ""}:
            raise ValueError(
                "embedding.revision must be a pinned commit hash, not a "
                "mutable ref like 'main'"
            )
        return v

    @field_validator("dimension")
    @classmethod
    def _dimension_is_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("embedding.dimension must be positive")
        return v


class CorpusConfig(_Strict):
    """Corpus location, kept out of code so it can move without touching call sites.
    required_docs_file is the 84-doc allowlist; must stay identical across C0-C4."""

    pdf_dir: str = "data/pdfs"
    required_docs_file: str = "data/required_docs.txt"


class GuardsConfig(_Strict):
    numeric_verbatim: bool = True
    deterministic_numeric_comparison: bool = True

    @field_validator("numeric_verbatim")
    @classmethod
    def _numeric_verbatim_always_on(cls, v: bool) -> bool:
        if not v:
            raise ValueError("guards.numeric_verbatim may never be disabled")
        return v

    @field_validator("deterministic_numeric_comparison")
    @classmethod
    def _deterministic_comparison_always_on(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "guards.deterministic_numeric_comparison may never be disabled"
            )
        return v


class ExperimentConfig(_Strict):
    experiment_id: str
    enable_npce: bool
    enable_cove: bool
    retrieval: RetrievalConfig
    indexing: IndexingConfig
    llm: LLMConfig
    embedding: EmbeddingConfig = EmbeddingConfig()
    corpus: CorpusConfig = CorpusConfig()
    guards: GuardsConfig = GuardsConfig()

    @model_validator(mode="after")
    def _shared_retrieval_core(self) -> "ExperimentConfig":
        # naive_dense/fixed_size/reranker-off is C0 territory only (outside the 2x2 factorial).
        # C1-C4 need an identical retrieval core or NPCE/CoVe deltas get confounded.
        is_naive = (
            self.retrieval.mode == "naive_dense"
            or self.indexing.granularity == "fixed_size"
            or not self.retrieval.enable_reranker
        )
        if is_naive and self.experiment_id != "c0_naive":
            raise ValueError(
                "naive_dense / fixed_size / reranker-off retrieval settings are "
                "reserved for c0_naive; C1-C4 must share an identical retrieval core"
            )
        return self


def load_config(path: Union[str, Path]) -> ExperimentConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return ExperimentConfig.model_validate(raw)
