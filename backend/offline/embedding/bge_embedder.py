"""Shared embedder — BAAI/bge-base-en-v1.5 via sentence-transformers.

One shared instance for both the placeholder registry and the index
writers — two embedding paths would silently give the system two notions
of "similar." Local, not API-based: keeps filings on-prem and results
reproducible instead of subject to a provider's silent model revisions.
Revision (commit hash) is pinned, not just the model name, since HF
checkpoints can update in place — re-pin deliberately and log the new
hash. encode_queries/encode_passages are split methods, not one
encode(is_query=...), so the asymmetric query prefix can't be forgotten.
"""
from __future__ import annotations

from typing import Sequence

MODEL_NAME = "BAAI/bge-base-en-v1.5"
# pinned commit hash, resolved via the HF Hub API -- see module docstring
MODEL_REVISION = "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"
EXPECTED_DIMENSION = 768

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def apply_query_prefix(texts: Sequence[str]) -> list[str]:
    """Asymmetric-prefix rule, pulled out as a pure function so it's
    testable without loading the actual model."""
    return [f"{QUERY_PREFIX}{t}" for t in texts]


class BGEEmbedder:
    """Concrete Embedder. Lazy model load — import/construction needs no
    network access, only the first encode/dimension call does."""

    def __init__(self, model_name: str = MODEL_NAME, revision: str = MODEL_REVISION) -> None:
        self._model_name = model_name
        self._revision = revision
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name, revision=self._revision)
        return self._model

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def dimension(self) -> int:
        return self._load().get_embedding_dimension()

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self._load().encode(list(texts), convert_to_numpy=True).tolist()

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._load().encode(apply_query_prefix(texts), convert_to_numpy=True).tolist()
