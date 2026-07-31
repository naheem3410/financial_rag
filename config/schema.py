"""Typed experiment configuration.

Drives all five experiment cells (C0-C4). `enable_npce` / `enable_cove`
select the `QueryTransformer` / `Verifier` implementation bound for the
run — that's the only thing that may change between cells; every other
setting needs to stay identical across C1-C4, which `_shared_retrieval_core`
below enforces.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RetrievalMode = Literal["hybrid", "naive_dense"]
IndexGranularity = Literal["proposition", "fixed_size"]
Provider = Literal["openai", "anthropic", "ollama"]

# Exactly six call sites. CoVe's three sub-calls (claim extraction,
# independent execution, prose comparison) share one config entry, since
# they're treated as one call site.
CALL_SITE_NAMES: tuple[str, ...] = (
    "proposition_segmenter",
    "numeric_qualifier_resolver",
    "query_splitter",
    "fff_rewriter",
    "generator",
    "cove_verification",
)

# Not a seventh call site. `proposition_segmenter_escalation` performs the
# identical task as `proposition_segmenter` (segment text into
# propositions; same schema, same numeric-verbatim guard) on a fallback
# model, used only when a batch fails the deterministic table-row
# completeness check
# (offline/propositions/segmenter_llm.py:_table_rows_complete) — a
# reliability mechanism for the segmenter, not a new kind of LLM judgment.
# Optional: a config that omits it gets no escalation (offline/full_build.py
# checks for its presence before building an escalation client).
OPTIONAL_CALL_SITE_NAMES: tuple[str, ...] = ("proposition_segmenter_escalation",)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalConfig(_Strict):
    mode: RetrievalMode = "hybrid"
    top_k: int = 20
    rerank_top_k: int = 5
    enable_reranker: bool = True
    time_weighting: bool = True


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
    """One of the six call sites, independently configurable — provider and
    model are a YAML edit, never a code change. `include_reasoning_field`
    defaults `True`: putting the reasoning field first preserves reasoning
    quality under constrained decoding. The proposition segmenter is
    piloted with it `False` as a deliberate exception — segmentation is
    mechanical instruction-following, not reasoning, so the field is pure
    cost with no quality benefit there."""

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
        # Minimal guard, not exhaustive per-provider knowledge: catches the
        # obvious cases ("latest", empty). A provider-specific dated
        # snapshot (e.g. "gpt-4.1-mini-2025-04-14") is the caller's
        # responsibility to supply.
        if v.strip().lower() in {"latest", ""}:
            raise ValueError(f"call-site model must be a pinned snapshot, not {v!r}")
        return v


class ProviderConnectionConfig(_Strict):
    """Per-provider connection + rate-limit settings. Hosted APIs (OpenAI,
    Anthropic) don't guarantee determinism from a seed even when one is
    accepted — only a local/self-hosted provider (Ollama) can. RPM/TPM/RPD
    are enforced by `llm/routing.py`'s rate limiter, not hardcoded in an
    adapter."""

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
                "-- exactly six call sites, all must be configured"
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
    """Shared embedder identity. Recorded in the run manifest exactly like
    the LLM's pinned version — an unpinned or silently-updated embedding
    checkpoint breaks reproducibility the same way an unpinned LLM
    would."""

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
    """Corpus location — a config field, not a hardcoded literal, so the
    path can move without touching call sites. `pdf_dir` holds the full
    FinanceBench PDF set; `required_docs_file` is the 84-document allowlist
    derived from `doc_name` in the FinanceBench question set — corpus
    scope must be identical across C0-C4, so both fields are shared
    config, never per-cell."""

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
        # naive_dense / fixed_size / reranker-off settings are reserved for
        # C0, which sits outside the 2x2 factorial. C1-C4 must share one
        # identical retrieval core or the NPCE/CoVe deltas are confounded
        # with retrieval differences.
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
