"""Retrieval relevance and Hit Rate/MRR/NDCG.

Relevance definition: FinanceBench gives a gold evidence page number plus
source document per question; the retriever returns propositions, not
evidence spans. A retrieved proposition counts as relevant to a question
if its `metadata.source_uri` and `metadata.page` match one of that
question's gold evidence references — page-level relevance, not text-span
overlap.

The zero-indexing convention (mandatory): `evidence_page_num` in the
FinanceBench dataset is zero-indexed. `Proposition.metadata.page` is also
zero-indexed — the layout/table parser assigns it via
`enumerate(pdf.pages)` (offline/parsers/layout_parser.py), which starts at
0. `gold_refs_from_financebench_evidence` below performs no +1/-1
adjustment anywhere: an off-by-one here would silently depress Hit
Rate/MRR/NDCG uniformly across every cell and every question, and would
look like a weak retriever rather than what it actually is — a unit
mismatch. `tests/test_retrieval_metrics.py` asserts this explicitly, not
just implicitly through end-to-end numbers.

All three metrics use binary relevance (0/1) — FinanceBench's evidence
annotation doesn't grade relevance by degree, just presence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from contracts.models import Proposition


@dataclass(frozen=True)
class GoldEvidenceRef:
    """One gold evidence reference for a question: source document +
    zero-indexed page. A question may have more than one (comparative
    statements, multi-page evidence)."""

    source_uri: str
    page: int


def gold_refs_from_financebench_evidence(
    evidence: Sequence[dict], source_uri: str
) -> list[GoldEvidenceRef]:
    """Field mapping: `evidence[].evidence_page_num` -> `GoldEvidenceRef.page`,
    unchanged. Both `evidence_page_num` and `Proposition.metadata.page` are
    zero-indexed — this function is the one place that mapping happens, so
    there's exactly one place an off-by-one could be introduced, and it's
    asserted not to be here."""
    return [GoldEvidenceRef(source_uri=source_uri, page=e["evidence_page_num"]) for e in evidence]


def gold_refs_from_collision_record(record: dict) -> list[GoldEvidenceRef]:
    """FinanceBench-Collision schema field mapping — distinct from
    `gold_refs_from_financebench_evidence` above, which reads the flat
    stock FinanceBench schema (`evidence[].evidence_page_num`). Here, gold
    evidence lives at `source.evidence_refs[].page_num_zero_indexed`, and
    `evidence_refs[].doc_name` is nullable, meaning "same as
    `source.primary_doc`" — the same null-resolution rule
    `offline/gold_overlap.py:gold_evidence_pages` uses for the build-time
    safety assertion, applied here per-question instead of collapsed to a
    per-doc set, since Hit Rate/MRR/NDCG need to know which gold pages
    belong to this question. No +1/-1 adjustment, for the same reason as
    the sibling function: `page_num_zero_indexed` and
    `Proposition.metadata.page` are both zero-indexed already.
    """
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
    """1 / (1-indexed rank of the first relevant proposition); 0.0 if none
    in the ranked list are relevant."""
    for rank, proposition in enumerate(ranked_propositions, start=1):
        if is_relevant(proposition, gold_refs):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    ranked_propositions: Sequence[Proposition], gold_refs: Sequence[GoldEvidenceRef], k: int
) -> float:
    """Binary-relevance NDCG@k.

    IDCG needs care here because relevance is page-level (`is_relevant`): a
    single gold page can carry many relevant propositions (real financial
    statement pages routinely do — dozens of line items). Assuming exactly
    `min(len(gold_refs), k)` relevant items exist — one per gold page
    reference — undercounts whenever more than one relevant proposition
    from the same page surfaces in the ranking, silently producing NDCG >
    1.0. IDCG is instead `min(actual relevant count within
    ranked_propositions, k)` — the best DCG achievable by any reordering of
    the same candidates DCG is computed from, which makes DCG <= IDCG true
    by construction (IDCG's relevant-item count is drawn from a superset of
    whatever DCG's top-k slice can contain), so NDCG <= 1.0 always, not
    just when relevance happens to be 1:1 with gold pages.
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
