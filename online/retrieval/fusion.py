"""Rank fusion + time-aware weighting. No model involved.

Reciprocal Rank Fusion, k=60 (the standard constant from Cormack, Clarke &
Buettcher 2009), combines the dense and sparse arms' rankings — not their
raw scores, which live on incomparable scales (cosine similarity vs.
BM25). Time-aware weighting then nudges the fused ranking toward more
recent filings when a sub-query carries no explicit period — the
"unconstrained period" sentinel from the query splitter, not text-pattern
matching on words like "latest".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from contracts.models import Proposition
from online.retrieval.hybrid import ScoredProposition

RRF_K = 60

# Linear recency multiplier: the most recently filed candidate gets weight
# 1.0; the oldest gets 1.0 - RECENCY_WEIGHT_SPREAD. Kept
# small deliberately: RRF (k=60) already produces small score gaps between
# adjacent ranks, so the nudge must stay well under that gap or it stops
# being a tie-breaker and starts overriding genuine relevance rank — a
# gentle nudge, not a reordering strong enough to beat a real match with a
# stale one (see tests/test_fusion.py for the calibration this satisfies).
RECENCY_WEIGHT_SPREAD = 0.01


@dataclass(frozen=True)
class FusedResult:
    proposition: Proposition
    rrf_score: float


def reciprocal_rank_fusion(scored: Sequence[ScoredProposition], k: int = RRF_K) -> list[FusedResult]:
    """RRF: `sum(1 / (k + rank))` across arms, ranked positions 1-indexed
    within each arm."""
    if not scored:
        return []

    by_dense = sorted(scored, key=lambda s: s.dense_score, reverse=True)
    by_sparse = sorted(scored, key=lambda s: s.sparse_score, reverse=True)
    dense_rank = {s.proposition.proposition_id: i for i, s in enumerate(by_dense, start=1)}
    sparse_rank = {s.proposition.proposition_id: i for i, s in enumerate(by_sparse, start=1)}

    fused = [
        FusedResult(
            proposition=s.proposition,
            rrf_score=(
                1.0 / (k + dense_rank[s.proposition.proposition_id])
                + 1.0 / (k + sparse_rank[s.proposition.proposition_id])
            ),
        )
        for s in scored
    ]
    fused.sort(key=lambda f: f.rrf_score, reverse=True)
    return fused


def apply_time_weighting(fused: Sequence[FusedResult], enabled: bool) -> list[FusedResult]:
    """No-op unless `enabled` (bound to `config.retrieval.time_weighting`
    by the caller) — every cell in the 2x2 factorial needs to use the same
    setting, since they all share an identical retrieval core."""
    if not enabled or not fused:
        return list(fused)

    def _filing_date(result: FusedResult) -> date:
        return date.fromisoformat(result.proposition.metadata.filing_date)

    by_recency = sorted(fused, key=_filing_date, reverse=True)
    n = len(by_recency)
    weight_by_id = {
        r.proposition.proposition_id: 1.0 - RECENCY_WEIGHT_SPREAD * (i / max(n - 1, 1))
        for i, r in enumerate(by_recency)
    }

    reweighted = [
        FusedResult(
            proposition=r.proposition, rrf_score=r.rrf_score * weight_by_id[r.proposition.proposition_id]
        )
        for r in fused
    ]
    reweighted.sort(key=lambda f: f.rrf_score, reverse=True)
    return reweighted
