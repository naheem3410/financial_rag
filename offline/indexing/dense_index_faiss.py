"""Persistent dense index — FAISS.

No learning or generation here — exact nearest-neighbor search over
already-computed embeddings. Cosine similarity via inner product over
L2-normalized vectors (`IndexFlatIP`) — exact search is appropriate at this
corpus's scale (a few thousand propositions); swap for an approximate index
behind this same interface if the corpus ever grows past what exact search
can serve.

Persisted as two sidecar files: the FAISS index itself (`<path>.faiss`) and
a JSON array mapping FAISS's internal row order to `proposition_id`
(`<path>.ids.json`) — FAISS has no concept of string keys natively.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence, Union

import faiss
import numpy as np


def _l2_normalize(vector: Sequence[float]) -> list[float]:
    arr = np.array(vector, dtype="float32")
    norm = np.linalg.norm(arr)
    if norm == 0:
        return arr.tolist()
    return (arr / norm).tolist()


def _faiss_path(path: Union[str, Path]) -> Path:
    return Path(str(path) + ".faiss")


def _ids_path(path: Union[str, Path]) -> Path:
    return Path(str(path) + ".ids.json")


class PersistentDenseIndex:
    def __init__(self, dimension: int):
        self.dimension = dimension
        self._index = faiss.IndexFlatIP(dimension)
        self._ids: list[str] = []  # row i -> proposition_id

    def upsert(self, proposition_id: str, vector: Sequence[float]) -> None:
        if len(vector) != self.dimension:
            raise ValueError(
                f"PersistentDenseIndex dimension mismatch: index expects "
                f"{self.dimension}-d vectors, got {len(vector)}-d for "
                f"proposition_id={proposition_id!r}. Refusing to write a "
                "corrupt index."
            )
        if proposition_id in self._ids:
            raise ValueError(
                f"PersistentDenseIndex: {proposition_id!r} already present — "
                "a flat FAISS index has no in-place update; rebuild the "
                "index rather than upserting an existing id."
            )
        self._index.add(np.array([_l2_normalize(vector)], dtype="float32"))
        self._ids.append(proposition_id)

    def ids(self) -> set[str]:
        return set(self._ids)

    def search(self, query_vector: Sequence[float], k: int) -> list[tuple[str, float]]:
        if not self._ids:
            return []
        scores, indices = self._index.search(
            np.array([_l2_normalize(query_vector)], dtype="float32"), min(k, len(self._ids))
        )
        return [
            (self._ids[idx], float(score)) for score, idx in zip(scores[0], indices[0]) if idx != -1
        ]

    def save(self, path: Union[str, Path]) -> None:
        faiss.write_index(self._index, str(_faiss_path(path)))
        _ids_path(path).write_text(json.dumps(self._ids), encoding="utf-8")

    @classmethod
    def load(cls, path: Union[str, Path], dimension: int) -> "PersistentDenseIndex":
        instance = cls(dimension=dimension)
        instance._index = faiss.read_index(str(_faiss_path(path)))
        instance._ids = json.loads(_ids_path(path).read_text(encoding="utf-8"))
        return instance
