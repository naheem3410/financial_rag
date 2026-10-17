"""Shared test doubles. Not a test module itself (pytest only collects
test_*.py), just helpers imported by ones that are.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Sequence


class FakeEmbedder:
    """Deterministic `Embedder` double, no real model or network.

    Pass `vectors` to pin exact output when a test needs real similarity
    (e.g. collision detection); unlisted text falls back to a hash vector.
    """

    def __init__(
        self,
        dimension: int = 8,
        vectors: Optional[dict] = None,
        model_name: str = "fake-embedder",
        revision: str = "test",
    ) -> None:
        self._dimension = dimension
        self._explicit_vectors = vectors or {}
        self._model_name = model_name
        self._revision = revision
        self.passage_calls: list[str] = []
        self.query_calls: list[str] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> list[float]:
        if text in self._explicit_vectors:
            return list(self._explicit_vectors[text])
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255.0 for b in digest[: self._dimension]]

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]:
        self.passage_calls.extend(texts)
        return [self._vector(t) for t in texts]

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        self.query_calls.extend(texts)
        return [self._vector(t) for t in texts]
