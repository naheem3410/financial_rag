"""Structured LLM I/O schemas — one pydantic model per call site, validated
on receipt. `extra="forbid"` so a hallucinated field gets rejected structurally.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DIGIT_RE = re.compile(r"\d")


class NumericQualifierResolution(BaseModel):
    """The model classifies, never re-emits a numeric value. Digit check is
    field-scoped to `reasoning` only — `fiscal_year` etc. legitimately hold digits."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    fiscal_year: Optional[int] = None
    quarter: Optional[Literal[1, 2, 3, 4]] = None
    basis: Optional[Literal["reported", "ttm", "restated"]] = None
    scale: Optional[Literal["unit", "thousands", "millions", "billions"]] = None
    currency: Optional[str] = None
    resolvable: bool

    @field_validator("reasoning")
    @classmethod
    def _no_digits_in_free_text(cls, v: str) -> str:
        if _DIGIT_RE.search(v):
            raise ValueError(
                "reasoning is free text and must contain no digits — a numeric "
                "value belongs in a declared field (fiscal_year/quarter/...), "
                "never in prose"
            )
        return v


class SegmentedPropositionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    source_offset_hint: Optional[str] = None


class PropositionSegmenterOutput(BaseModel):
    """Proposition segmenter output. `reasoning` comes first — ordering
    it before the answer fields preserves quality under constrained decoding."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    propositions: list[SegmentedPropositionItem]


class PropositionSegmenterOutputNoReasoning(BaseModel):
    """`reasoning` dropped — segmentation is mechanical, not reasoning, so
    the field is pure cost here. Selected via config, never hardcoded."""

    model_config = ConfigDict(extra="forbid")

    propositions: list[SegmentedPropositionItem]


class QuerySplitterPeriodItem(BaseModel):
    """One period slot. `quarter` absent/null means a full fiscal year, not Q1."""

    model_config = ConfigDict(extra="forbid")

    fiscal_year: int
    quarter: Optional[int] = None


class QuerySplitterOutput(BaseModel):
    """Query splitter output. The entities x metrics x periods split is a
    deterministic Cartesian product downstream; this call only parses slots."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    entities: list[str]
    metrics: list[str]
    unmapped_metrics: list[str] = Field(default_factory=list)
    periods: list[QuerySplitterPeriodItem] = Field(default_factory=list)
    filing_types: list[str] = Field(default_factory=list)


class MetricLabelResolution(BaseModel):
    """Metric-label fallback (step 1.4, call site: metric_label_fallback).
    The model only ever picks from the lexicon it was given -- code drops
    any name it returns that isn't in that list, the same "classify, never
    invent" rule numeric_extractor's tier 3 already follows."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    metrics: list[str]


class AntecedentExtractionOutput(BaseModel):
    """Antecedent extraction fallback (step 3.3's policy work, call site:
    antecedent_extraction_fallback). The model may only quote or closely
    paraphrase who/when a rule block already says it applies to -- it must
    never add a condition the text doesn't state. Code additionally checks
    the returned phrase actually shares real words with the source block
    before trusting it, the same "classify/extract, never invent" rule
    every other fallback call site in this project follows."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    antecedent: Optional[str] = None  # None is a correct answer: "this rule states no specific antecedent"


class LookAlikeGroupOutput(BaseModel):
    """Paper-split look-alike fallback (step 4.3, call site:
    paper_split_fallback, owner's yes given 2026-09-26). Only reached when
    the free rule-based check (online/routing/paper_splitter.py's
    detect_plain_comparison) found no plain "X vs Y" shape to split on.
    The model may only quote phrases already in the question -- code
    additionally checks every phrase it returns actually appears there
    (find_grounded_phrases) before trusting any of them, the same
    "classify/extract, never invent" rule every other fallback follows."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    phrases: list[str] = Field(default_factory=list)  # empty is correct: "nothing to split here"


class StepPlanStepItem(BaseModel):
    """One planned step. `question` may reference an earlier step's answer
    with "{step_N}" (N must be a smaller step_id -- code rejects anything
    else, a step can never depend on itself or a step that hasn't run yet,
    see online/routing/step_planner.py's own validation)."""

    model_config = ConfigDict(extra="forbid")

    step_id: int
    question: str


class StepPlanOutput(BaseModel):
    """Step planner output (step 4.6, call site: step_planner, owner's yes
    given 2026-09-26 -- the project's own written rule of six call sites is
    changed, in writing, to seven for this one). The model only ever plans
    steps; it never answers anything itself, and every step it proposes is
    checked by code (step_planner.validate_step_plan) before any step ever
    runs -- ids must be a plain 1..N sequence, no step may reference its
    own or a later step (a loop), and the whole plan is capped."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    steps: list[StepPlanStepItem] = Field(default_factory=list)  # empty is correct: "no steps needed"


class FFFRewriterFactItem(BaseModel):
    """One candidate fact from the FFF rewriter — still subject to the
    deterministic numeric guard after parsing; this only enforces shape."""

    model_config = ConfigDict(extra="forbid")

    proposition_id: str
    subject: str
    metric: str
    period_label: str
    value_text: str


class FFFRewriterOutput(BaseModel):
    """FFF rewriter output, guarded by numeric-verbatim validation downstream."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    facts: list[FFFRewriterFactItem]


class GeneratorAnswerItem(BaseModel):
    """One (metric, value) pair. A list of these, not a bare field, lets the
    NPCE-off baseline answer multi-metric questions in one sub-query."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    answer_value: str
    answer_unit: str


class GeneratorOutput(BaseModel):
    """Generator output. `answers` empty when `answerable` is false; every
    value still subject to the deterministic verbatim/fact-reference guard."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    answerable: bool
    answers: list[GeneratorAnswerItem]
    answer_text: str
    used_fff_ids: list[str]


class NaiveGeneratorOutput(BaseModel):
    """C0 (naive RAG baseline) only — reuses the generator's config slot
    but with a genuinely different, un-guarded prompt, no FFF atomization."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    answerable: bool
    answers: list[GeneratorAnswerItem] = Field(default_factory=list)
    answer_text: str = ""
    used_chunk_ids: list[str] = Field(default_factory=list)


class PlainAIOutput(BaseModel):
    """Plain AI baseline (step 5.1, decision 1, call site: plain_ai_baseline,
    owner's yes given 2026-10-10). The model answers using only its own
    training knowledge -- no retrieval, no facts, nothing grounded. The
    opposite of every other call site in this project: here the model's own
    unverified claim IS the whole answer, by design, since the entire point
    is to measure what a bare model gets right on its own, as the floor the
    real system is compared against."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    answerable: bool
    answers: list[GeneratorAnswerItem] = Field(default_factory=list)
    answer_text: str = ""


Scale = Literal["unit", "thousands", "millions", "billions"]


class ClaimExtractionItem(BaseModel):
    """One atomic claim extracted from the draft for verification.
    `asserted_value`/`unit`/`scale` are `None` for non-numeric claims."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_text: str
    entity: str
    metric: str
    period_label: str
    is_numeric: bool
    asserted_value: Optional[str] = None
    asserted_unit: Optional[str] = None
    scale: Optional[Scale] = None


class ClaimExtractionOutput(BaseModel):
    """Claim extraction output. Verification questions are generated from
    these by a deterministic template, not part of this LLM call."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    claims: list[ClaimExtractionItem]


class IndependentVerificationOutput(BaseModel):
    """Independent verification output. Draft is structurally absent from
    the input (CLAUDE.md §6.12.3); period_label/scale come from the evidence, not the claim."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    supported: bool
    answer_value: Optional[str] = None
    answer_unit: Optional[str] = None
    period_label: Optional[str] = None
    scale: Optional[Scale] = None
    supporting_fff_ids: list[str] = Field(default_factory=list)


class ProseComparisonOutput(BaseModel):
    """Prose comparison output, non-numeric claims only."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    verdict: Literal["match", "conflict", "unsupported"]
