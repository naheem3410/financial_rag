"""Structured LLM I/O schemas.

Every LLM call declares a JSON schema and gets validated on receipt. One
pydantic model per call site's output, `extra="forbid"` so an undeclared
field — a hallucinated one — gets rejected structurally instead of silently
accepted.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DIGIT_RE = re.compile(r"\d")


class NumericQualifierResolution(BaseModel):
    """Numeric qualifier resolution. The model classifies; it never
    re-emits a numeric value.

    Validation is field-scoped, not a blanket digit scan: `fiscal_year`
    legitimately holds a digit (e.g. `2017`) because it is one of the
    declared enumerations below — rejecting it would break the call site
    entirely. The rule that actually matters is that `reasoning` (free text)
    carries no digits at all, and that no field outside this exact declared
    set exists — the latter enforced by `extra="forbid"`, so a hallucinated
    field (e.g. a stray `"value": "42"`) is rejected structurally rather than
    by scanning output text for stray digits.
    """

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
    """Proposition segmenter output. `reasoning` is first in the schema —
    putting it before the answer fields preserves reasoning quality under
    constrained decoding."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    propositions: list[SegmentedPropositionItem]


class PropositionSegmenterOutputNoReasoning(BaseModel):
    """Proposition segmenter output with `reasoning` dropped — a deliberate
    exception for this call site only: segmentation is mechanical
    instruction-following, not reasoning, so the field is pure cost (billed
    as output tokens) with no quality benefit here. Selected via
    `llm.call_sites.proposition_segmenter.include_reasoning_field: false`
    (config/schema.py), never hardcoded."""

    model_config = ConfigDict(extra="forbid")

    propositions: list[SegmentedPropositionItem]


class QuerySplitterPeriodItem(BaseModel):
    """One period slot from the query splitter's output. `quarter`
    absent/null means a full fiscal year, not Q1."""

    model_config = ConfigDict(extra="forbid")

    fiscal_year: int
    quarter: Optional[int] = None


class QuerySplitterOutput(BaseModel):
    """Query splitter output. The split itself — `entities x metrics x
    periods` — is a deterministic Cartesian product downstream
    (`online/analysis/query_splitter.py`), never decided by the model; this
    call only parses slots. `unmapped_metrics`/`filing_types` are
    logged/passed through, not validated here — see the module's own
    validation notes."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    entities: list[str]
    metrics: list[str]
    unmapped_metrics: list[str] = Field(default_factory=list)
    periods: list[QuerySplitterPeriodItem] = Field(default_factory=list)
    filing_types: list[str] = Field(default_factory=list)


class FFFRewriterFactItem(BaseModel):
    """One candidate fact from the FFF rewriter. `value_text`/`subject`/
    `proposition_id` are all still subject to the mandatory deterministic
    guard (online/curation/numeric_guard.py, online/curation/
    fff_rewriter.py) after parsing; this schema only enforces shape, not
    truthfulness. `source_ref` is deliberately absent here — it's joined
    deterministically from proposition metadata, never taken from model
    output."""

    model_config = ConfigDict(extra="forbid")

    proposition_id: str
    subject: str
    metric: str
    period_label: str
    value_text: str


class FFFRewriterOutput(BaseModel):
    """FFF rewriter output."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    facts: list[FFFRewriterFactItem]


class GeneratorAnswerItem(BaseModel):
    """One (metric, value) pair in the generator's answer.

    A single-metric shape (bare `answer_value`/`answer_unit` fields) works
    fine when NPCE has already split a query down to one metric per
    sub-query. But with NPCE off, there's no split: a metric-axis question
    like "3M's accounts receivable and total current assets" arrives as one
    sub-query covering two metrics. Generalizing to a list of these items —
    one per metric actually asked — lets the NPCE-off baseline report a
    complete answer instead of being capped at one metric regardless of
    retrieval quality, which would otherwise confound the comparison with a
    schema artifact rather than a real effect."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    answer_value: str
    answer_unit: str


class GeneratorOutput(BaseModel):
    """Generator output. `answers` is empty when `answerable` is false —
    this and every value inside it are still subject to the mandatory
    deterministic guard after parsing (every `answer_value` must appear
    verbatim in a supplied fact; every `used_fff_ids` entry must reference
    a real fact), same principle as the FFF rewriter's guard."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    answerable: bool
    answers: list[GeneratorAnswerItem]
    answer_text: str
    used_fff_ids: list[str]


class NaiveGeneratorOutput(BaseModel):
    """C0 (naive RAG baseline) only. Reuses the generator's config slot via
    `schema_key="naive_generator"` (`llm/routing.py`), with a genuinely
    different, un-guarded prompt — see `online/output/naive_generator.py`
    for why C0 has no numeric-verbatim guard and no FFF atomization, unlike
    `GeneratorOutput` above."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    answerable: bool
    answers: list[GeneratorAnswerItem] = Field(default_factory=list)
    answer_text: str = ""
    used_chunk_ids: list[str] = Field(default_factory=list)


Scale = Literal["unit", "thousands", "millions", "billions"]


class ClaimExtractionItem(BaseModel):
    """One atomic claim extracted from the draft answer for verification.

    `asserted_value`/`asserted_unit`/`scale` are `None` for non-numeric
    claims (`is_numeric: false`) — a prose claim ("the increase was driven
    by organic growth") has no value/unit/scale to assert. `scale` is
    declared explicitly here because `numeric_match`
    (online/verification/numeric_comparator.py) compares `claim.scale`
    directly, so it can't be left undeclared."""

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
    these claims by a deterministic template
    (online/verification/cove_orchestrator.py) — not part of this LLM
    call."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    claims: list[ClaimExtractionItem]


class IndependentVerificationOutput(BaseModel):
    """Independent verification output. The draft is structurally absent
    from this call's input
    (online/verification/cove_orchestrator.py:execute_independent_verification
    takes no `draft` parameter — enforced by `test_cove_independence`).

    `period_label` and `scale` matter because `numeric_match` compares both
    sides' values, so this independently-executed answer has to state its
    own — from the evidence's own FFF records, not copied from the claim —
    for the comparison to mean anything instead of trivially confirming
    itself. This is exactly what lets the comparator catch a temporal
    collision: evidence retrieved for the wrong fiscal year carries that
    year's own true `period_label`, which then disagrees with the claim's
    asserted period."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    supported: bool
    answer_value: Optional[str] = None
    answer_unit: Optional[str] = None
    period_label: Optional[str] = None
    scale: Optional[Scale] = None
    supporting_fff_ids: list[str] = Field(default_factory=list)


class ProseComparisonOutput(BaseModel):
    """Prose comparison output, for non-numeric claims only."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str
    verdict: Literal["match", "conflict", "unsupported"]
