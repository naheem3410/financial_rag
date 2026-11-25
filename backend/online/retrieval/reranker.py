"""Cross-encoder reranker (not a generative LLM, not one of the six call
sites). Precision pass over the fused top-k -- bi-encoder retrieval alone
is too coarse for near-identical financial line items.

Uses BAAI/bge-reranker-base pinned by revision hash, same reproducibility
rationale as the shared embedder.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from contracts.models import Proposition, RequestContext, SubQuery
from online.retrieval.ask_match import ask_match, kind_tag

MODEL_NAME = "BAAI/bge-reranker-base"
# pinned commit hash, resolved via the HuggingFace Hub API
MODEL_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"
_EPS = 1e-12


def render_query_text(sq: SubQuery, ctx: Optional[RequestContext] = None) -> str:
    """Query side of the (query_text, passage_text) pair, built from the
    sub-query's resolved slots, not the original question.

    With NPCE on, `sq.entity` is an opaque placeholder (`<ENT_3>`), never a
    real company name -- but the passage text handed to the cross-encoder
    always has the real name (the index never stores placeholders). A
    cross-encoder isn't an LLM and can't blend two real names the way NPCE
    guards against, so feeding it the placeholder only blinds it to the
    entity match: it was scoring every candidate on topic words alone.
    `ctx.placeholder_map` resolves it back, same lookup
    `metadata_filter.resolve_company` already does. No `ctx`, or the
    placeholder isn't in the map (NPCE off, c1/c2): `sq.entity` is already
    the real name, unchanged."""
    entity = sq.entity
    if ctx is not None:
        entity = ctx.placeholder_map.get(sq.entity, sq.entity)
    metric_label = sq.metric.replace("_", " ") if sq.metric else ""
    return " ".join(part for part in (entity, metric_label, sq.period_label) if part)


@dataclass(frozen=True)
class RerankOptions:
    """Extra deterministic layers on top of the model's score. All off by
    default, so a plain `CrossEncoderReranker()` behaves exactly as before."""

    # Candidates whose logit is within this much of a group's best are a tie,
    # settled by ask-match (kind, then year). None turns it off.
    tie_break_logit_margin: Optional[float] = None
    # Prepend a plain kind tag ("balance at period end"...) to the text the model scores.
    tag_passages: bool = False
    # Add these multiples of the kind/year signals to the logit before sorting.
    blend_kind_weight: float = 0.0
    blend_year_weight: float = 0.0

    @classmethod
    def from_retrieval_config(cls, retrieval: Any) -> "RerankOptions":
        return cls(
            tie_break_logit_margin=retrieval.rerank_tie_break_logit_margin,
            tag_passages=retrieval.rerank_tag_passages,
            blend_kind_weight=retrieval.rerank_blend_kind_weight,
            blend_year_weight=retrieval.rerank_blend_year_weight,
        )


def _logit(probability: float) -> float:
    # The model's sigmoid saturates near 1.0, so gaps are measured in logit space.
    p = min(max(float(probability), _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def render_passage_text(p: Proposition) -> str:
    """Model-only input text, never shown to users. Only the kind tag is added:
    adding the metric label and year as well tested worse on real scores."""
    tag = kind_tag(p)
    return f"[{tag}] {p.text}" if tag else p.text


def _final_logits(
    sq: SubQuery, props: Sequence[Proposition], scores: Sequence[float], options: RerankOptions
) -> list[float]:
    logits = [_logit(s) for s in scores]
    if options.blend_kind_weight or options.blend_year_weight:
        signals = [ask_match(sq, p) for p in props]
        logits = [
            lg + options.blend_kind_weight * sig.kind + options.blend_year_weight * sig.year
            for lg, sig in zip(logits, signals)
        ]
    return logits


def order_candidates(
    sq: SubQuery, props: Sequence[Proposition], scores: Sequence[float], options: RerankOptions
) -> list[int]:
    """Indexes of `props`, best first. With every option off this is the
    plain score sort the reranker always did."""
    logits = _final_logits(sq, props, scores, options)
    signals = [ask_match(sq, p) for p in props]
    order = sorted(range(len(props)), key=lambda i: (logits[i], scores[i]), reverse=True)
    margin = options.tie_break_logit_margin
    if margin is None:
        return order

    # Walk down the sorted list; a candidate joins the current group while
    # it sits within `margin` of the group's first (best) member.
    result: list[int] = []
    group: list[int] = []
    for i in order:
        if group and logits[group[0]] - logits[i] > margin:
            result.extend(sorted(group, key=lambda j: (signals[j].kind, signals[j].year, logits[j]), reverse=True))
            group = []
        group.append(i)
    result.extend(sorted(group, key=lambda j: (signals[j].kind, signals[j].year, logits[j]), reverse=True))
    return result


class CrossEncoderReranker:
    """Concrete Reranker. Loads the model lazily on first use, so import
    and construction need no network access."""

    def __init__(
        self, model_name: str = MODEL_NAME, revision: str = MODEL_REVISION, options: Optional[RerankOptions] = None
    ) -> None:
        self._model_name = model_name
        self._revision = revision
        self._options = options or RerankOptions()
        self._model = None
        # Decision 38: pipeline_c4.py now calls `rerank` from several
        # threads at once, so two sub-queries' first calls could otherwise
        # both see `self._model is None` and load the (large) model twice.
        # Not a correctness bug either way -- just a double-checked lock
        # against the wasted load.
        self._load_lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from sentence_transformers import CrossEncoder

                    self._model = CrossEncoder(self._model_name, revision=self._revision)
        return self._model

    @classmethod
    def from_config(cls, retrieval: Any) -> "CrossEncoderReranker":
        return cls(options=RerankOptions.from_retrieval_config(retrieval))

    @property
    def options(self) -> RerankOptions:
        return self._options

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def revision(self) -> str:
        return self._revision

    def rerank(
        self, sq: SubQuery, props: Sequence[Proposition], k: int, ctx: Optional[RequestContext] = None
    ) -> Sequence[Proposition]:
        return [p for p, _ in self.rerank_scored(sq, props, k, ctx)]

    def rerank_scored(
        self, sq: SubQuery, props: Sequence[Proposition], k: int, ctx: Optional[RequestContext] = None
    ) -> list[tuple[Proposition, float]]:
        """Same order as `rerank`, each paired with its logit (after any blend)."""
        if not props:
            return []
        scores = self._raw_scores(sq, props, ctx)
        order = order_candidates(sq, props, scores, self._options)
        logits = _final_logits(sq, props, scores, self._options)
        return [(props[i], logits[i]) for i in order[:k]]

    def _raw_scores(
        self, sq: SubQuery, props: Sequence[Proposition], ctx: Optional[RequestContext] = None
    ) -> list[float]:
        model = self._load()
        query_text = render_query_text(sq, ctx)
        if self._options.tag_passages:
            pairs = [(query_text, render_passage_text(p)) for p in props]
        else:
            pairs = [(query_text, p.text) for p in props]
        return [float(s) for s in model.predict(pairs)]
