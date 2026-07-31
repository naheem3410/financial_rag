"""Cross-encoder reranker.

A cross-encoder, not a generative LLM — don't confuse this with one of the
permitted LLM call sites. A precision pass over the fused top-k;
bi-encoder retrieval alone is too coarse for near-identical financial line
items — exactly the noun-phrase-collision problem this whole system exists
to address.

Uses `BAAI/bge-reranker-base` via `sentence-transformers.CrossEncoder`,
pinned by exact revision hash. Same rationale as the shared bi-encoder
embedder (`offline/embedding/bge_embedder.py`): reproducibility from the
run manifest, and the on-prem deployment argument for an audited finance
setting.
"""
from __future__ import annotations

from typing import Sequence

from contracts.models import Proposition, SubQuery

MODEL_NAME = "BAAI/bge-reranker-base"
# Pinned commit hash, resolved via the HuggingFace Hub API -- see module docstring.
MODEL_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"


def render_query_text(sq: SubQuery) -> str:
    """The cross-encoder scores (query_text, passage_text) pairs — this is
    the query side, built from the sub-query's already-resolved slots
    rather than the original natural-language question, since by this
    stage (after the query split) each sub-query concerns exactly one
    entity/metric/period."""
    metric_label = sq.metric.replace("_", " ") if sq.metric else ""
    return " ".join(part for part in (sq.entity, metric_label, sq.period_label) if part)


class CrossEncoderReranker:
    """Concrete `Reranker` (contracts/interfaces.py). Loads the model lazily
    on first use — importing this module or constructing an instance never
    requires network access or model init."""

    def __init__(self, model_name: str = MODEL_NAME, revision: str = MODEL_REVISION) -> None:
        self._model_name = model_name
        self._revision = revision
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name, revision=self._revision)
        return self._model

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def revision(self) -> str:
        return self._revision

    def rerank(self, sq: SubQuery, props: Sequence[Proposition], k: int) -> Sequence[Proposition]:
        if not props:
            return []
        model = self._load()
        query_text = render_query_text(sq)
        pairs = [(query_text, p.text) for p in props]
        scores = model.predict(pairs)
        ranked = sorted(zip(props, scores), key=lambda pair: pair[1], reverse=True)
        return [proposition for proposition, _ in ranked[:k]]
