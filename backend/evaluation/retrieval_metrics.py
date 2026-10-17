"""Retrieval relevance and Hit Rate/MRR/NDCG.

Relevance is page-level: a proposition counts as relevant if its
source_uri+page matches a gold evidence ref, not by text-span overlap.
Both evidence_page_num and Proposition.metadata.page are zero-indexed —
no +1/-1 anywhere here, or Hit Rate/MRR/NDCG silently tank and look like
a weak retriever instead of a unit bug. Relevance is binary (0/1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from contracts.models import Proposition


@dataclass(frozen=True)
class GoldEvidenceRef:
    """One gold evidence ref: source doc + zero-indexed page.
    A question can have several (comparative statements, multi-page)."""

    source_uri: str
    page: int


def gold_refs_from_financebench_evidence(
    evidence: Sequence[dict], source_uri: str
) -> list[GoldEvidenceRef]:
    """Maps evidence[].evidence_page_num -> GoldEvidenceRef.page, unchanged.
    Both sides are zero-indexed; this is the one place that mapping happens."""
    return [GoldEvidenceRef(source_uri=source_uri, page=e["evidence_page_num"]) for e in evidence]


def gold_refs_from_collision_record(record: dict) -> list[GoldEvidenceRef]:
    """Collision schema mapping (different shape than the stock FinanceBench one above).
    doc_name is nullable -> falls back to primary_doc, same rule as gold_overlap.py; still zero-indexed, no adjustment."""
    source = record["source"]
    primary_doc = source["primary_doc"]
    return [
        GoldEvidenceRef(
            source_uri=evidence_ref.get("doc_name") or primary_doc,
            page=evidence_ref["page_num_zero_indexed"],
        )
        for evidence_ref in source["evidence_refs"]
    ]


def is_relevant(proposition: Proposition, gold_refs: Sequence[GoldEvidenceRef]) -> bool:
    return any(
        proposition.metadata.source_uri == ref.source_uri and proposition.metadata.page == ref.page
        for ref in gold_refs
    )


def hit_rate_at_k(
    ranked_propositions: Sequence[Proposition], gold_refs: Sequence[GoldEvidenceRef], k: int
) -> float:
    """1.0 if any of the top-k retrieved propositions is relevant, else 0.0."""
    return 1.0 if any(is_relevant(p, gold_refs) for p in ranked_propositions[:k]) else 0.0


def reciprocal_rank(
    ranked_propositions: Sequence[Proposition], gold_refs: Sequence[GoldEvidenceRef]
) -> float:
    """1 / rank of first relevant proposition (1-indexed); 0.0 if none relevant."""
    for rank, proposition in enumerate(ranked_propositions, start=1):
        if is_relevant(proposition, gold_refs):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    ranked_propositions: Sequence[Proposition], gold_refs: Sequence[GoldEvidenceRef], k: int
) -> float:
    """Binary-relevance NDCG@k.

    IDCG uses the actual relevant count in ranked_propositions (capped at k),
    not len(gold_refs) — a single gold page can hold many relevant
    propositions, and the naive count would push NDCG above 1.0.
    """
    top_k = ranked_propositions[:k]
    dcg = sum(
        (1.0 if is_relevant(p, gold_refs) else 0.0) / math.log2(rank + 1)
        for rank, p in enumerate(top_k, start=1)
    )
    actual_relevant_count = sum(1 for p in ranked_propositions if is_relevant(p, gold_refs))
    ideal_relevant_count = min(actual_relevant_count, k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_relevant_count + 1))
    return dcg / idcg if idcg > 0 else 0.0
