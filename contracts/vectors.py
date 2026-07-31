"""Shared cosine similarity helper.

Pure Python, no numpy — used by components that work with small,
already-materialized vector lists (the placeholder registry builder's
collision clustering, the in-memory dense index, the hybrid retriever's
per-sub-query scoring). `offline/indexing/dense_index_faiss.py` uses numpy
directly instead, since FAISS already requires it for the persisted index.
"""
from __future__ import annotations

import math
from typing import Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
