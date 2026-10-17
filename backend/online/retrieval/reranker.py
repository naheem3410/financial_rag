"""Cross-encoder reranker (not a generative LLM, not one of the six call
sites). Precision pass over the fused top-k -- bi-encoder retrieval alone
is too coarse for near-identical financial line items.

Uses BAAI/bge-reranker-base pinned by revision hash, same reproducibility
rationale as the shared embedder.
"""
from __future__ import annotations

from typing import Sequence

from contracts.models import Proposition, SubQuery

MODEL_NAME = "BAAI/bge-reranker-base"
# pinned commit hash, resolved via the HuggingFace Hub API
MODEL_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"


def render_query_text(sq: SubQuery) -> str:
    """Query side of the (query_text, passage_text) pair, built from the
    sub-query's resolved slots, not the original question."""
    metric_label = sq.metric.replace("_", " ") if sq.metric else ""
    return " ".join(part for part in (sq.entity, metric_label, sq.period_label) if part)


class CrossEncoderReranker:
    """Concrete Reranker. Loads the model lazily on first use, so import
    and construction need no network access."""

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
