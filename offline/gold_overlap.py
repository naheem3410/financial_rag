"""Gold-overlap safety assertion — the check that makes the Risk Factors
section skip (`offline/parsers/section_skip.py`) and the general
content-based page filter (`offline/parsers/page_filter.py`) auditable
rather than merely assumed-safe.

Both skips are deliberately content-based and blind to gold evidence at
classification time (their own module docstrings say so). This module is
where that blindness gets checked, after the fact: cross-reference every
page actually skipped for a document — by either mechanism, plus any page an
OpenAI content-filter rejection forced a drop on — against that document's
gold evidence pages from the metric-axis question set
(`evaluation/data/financebench_collision_metric_axis.jsonl`). A skipped gold
page makes a question unanswerable; the build must stop, not proceed
silently, if that ever happens.

Reads only `source.primary_doc` / `source.evidence_refs[].page_num_zero_indexed`
— never `gold_answers`, which is a user-maintained, partially-annotated
field this codebase must never read, infer, or fill.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Union


def gold_evidence_pages(collision_jsonl_path: Union[str, Path]) -> dict[str, set[int]]:
    """`doc_name -> {zero-indexed gold evidence page numbers}`, resolving
    `evidence_refs[].doc_name: null` to that record's `source.primary_doc`
    (the schema's documented meaning of a null doc_name).
    """
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
    """Raised when a page that was skipped at index time carries gold
    evidence for a question in the metric-axis set — the skip made that
    question unanswerable, so the build must stop rather than proceed."""

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
    """`skipped_pages_by_doc` is `doc_name -> {page_number: reason}`, the
    union of every skip mechanism applied to that document (page filter,
    Risk Factors section skip, content-filter API rejections). Returns every
    overlap found, sorted for stable reporting; empty means the skip was
    safe for this question set.
    """
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
    """Raises `GoldPageSkippedError` if any skipped page overlaps gold
    evidence. Call this after indexing, before the run is reported as safe.
    """
    overlaps = find_gold_page_overlaps(gold_pages_by_doc, skipped_pages_by_doc)
    if overlaps:
        raise GoldPageSkippedError(overlaps)
