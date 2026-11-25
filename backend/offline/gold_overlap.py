"""Gold-overlap safety assertion — makes the Risk Factors skip and the
content-based page filter auditable instead of just assumed-safe.

Both skips are blind to gold evidence at classification time; this checks
after the fact that no skipped page was actually gold evidence for the
metric-axis question set — stop the build if one was.

Reads only primary_doc / evidence_refs[].page_num_zero_indexed, never
gold_answers (user-owned, must never be read/inferred/filled here).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Union


def gold_evidence_pages(collision_jsonl_path: Union[str, Path]) -> dict[str, set[int]]:
    """doc_name -> {zero-indexed gold page numbers}. A null doc_name in
    evidence_refs resolves to that record's primary_doc, per the schema."""
    pages_by_doc: dict[str, set[int]] = {}
    with open(collision_jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            source = record["source"]
            primary_doc = source["primary_doc"]
            for evidence_ref in source["evidence_refs"]:
                doc_name = evidence_ref.get("doc_name") or primary_doc
                pages_by_doc.setdefault(doc_name, set()).add(evidence_ref["page_num_zero_indexed"])
    return pages_by_doc


@dataclass(frozen=True)
class GoldPageOverlap:
    doc_name: str
    page_number: int
    skip_reason: str


class GoldPageSkippedError(RuntimeError):
    """A skipped page carried gold evidence, making a question
    unanswerable — build must stop rather than proceed."""

    def __init__(self, overlaps: list[GoldPageOverlap]) -> None:
        self.overlaps = overlaps
        detail = "; ".join(f"{o.doc_name} page {o.page_number} (skipped as {o.skip_reason!r})" for o in overlaps)
        super().__init__(
            f"{len(overlaps)} skipped page(s) carry gold evidence for the "
            f"metric-axis question set — these questions would be "
            f"unanswerable: {detail}"
        )


def find_gold_page_overlaps(
    gold_pages_by_doc: dict[str, set[int]],
    skipped_pages_by_doc: dict[str, dict[int, str]],
) -> list[GoldPageOverlap]:
    """skipped_pages_by_doc is doc_name -> {page_number: reason}, the union
    of every skip mechanism. Returns overlaps found, sorted; empty is safe."""
    overlaps: list[GoldPageOverlap] = []
    for doc_name, gold_pages in gold_pages_by_doc.items():
        skipped = skipped_pages_by_doc.get(doc_name, {})
        for page_number in sorted(gold_pages & skipped.keys()):
            overlaps.append(GoldPageOverlap(doc_name=doc_name, page_number=page_number, skip_reason=skipped[page_number]))
    return overlaps


def assert_no_gold_page_overlap(
    gold_pages_by_doc: dict[str, set[int]],
    skipped_pages_by_doc: dict[str, dict[int, str]],
) -> None:
    """Raises GoldPageSkippedError on any overlap. Call after indexing,
    before the run is reported safe."""
    overlaps = find_gold_page_overlaps(gold_pages_by_doc, skipped_pages_by_doc)
    if overlaps:
        raise GoldPageSkippedError(overlaps)
