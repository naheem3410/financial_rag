"""FinanceBench corpus scope — which documents to index.

Only the 84 docs the question set actually references, not the whole
pdfs/ directory — full corpus is ~4x the LLM cost and adds distractors
no question needs. Never rename a doc_name/pdf pair, it breaks the join
to gold evidence.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Union


def required_doc_names(financebench_jsonl_path: Union[str, Path]) -> set[str]:
    """Dedup'd `doc_name` across every FinanceBench question — this is the
    index scope, not the full corpus dir. evidence_doc_name is already a
    subset of this, verified separately, so no need to also scan it."""
    names: set[str] = set()
    with open(financebench_jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            names.add(record["doc_name"])
    return names


def required_doc_names_from_collision_set(collision_jsonl_path: Union[str, Path]) -> set[str]:
    """Metric-axis 5-doc subset, distinct from required_doc_names' 84-doc
    corpus. Reads primary_doc + evidence_refs[].doc_name (null means "same
    as primary_doc"). Never reads gold_answers — user-owned, do not touch.
    """
    names: set[str] = set()
    with open(collision_jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            source = record["source"]
            names.add(source["primary_doc"])
            for evidence_ref in source["evidence_refs"]:
                doc_name = evidence_ref.get("doc_name")
                if doc_name:
                    names.add(doc_name)
    return names


def assert_matches_required_docs(doc_names: set[str], required_docs_path: Union[str, Path]) -> None:
    """Derived doc_name set must exactly match data/required_docs.txt, or
    the question-document join may be broken. Fail loud before an
    expensive indexing run, not after."""
    required = {
        line.strip() for line in Path(required_docs_path).read_text(encoding="utf-8").splitlines() if line.strip()
    }
    if doc_names != required:
        missing_from_derived = sorted(required - doc_names)
        extra_in_derived = sorted(doc_names - required)
        raise ValueError(
            "Derived document set does not match "
            f"{required_docs_path} — the join between questions and "
            f"documents may be broken. Missing from derived: "
            f"{missing_from_derived}. Extra in derived: {extra_in_derived}."
        )


def resolve_pdf_paths(doc_names: set[str], pdf_dir: Union[str, Path]) -> dict[str, Path]:
    """doc_name -> .pdf path under pdf_dir. Raises if anything required is
    missing, rather than discovering the gap mid indexing-run."""
    pdf_dir = Path(pdf_dir)
    resolved: dict[str, Path] = {}
    missing: list[str] = []
    for name in sorted(doc_names):
        path = pdf_dir / f"{name}.pdf"
        if path.exists():
            resolved[name] = path
        else:
            missing.append(name)
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} required document(s) missing from {pdf_dir}: {missing}"
        )
    return resolved


_DOC_NAME_RE = re.compile(r"^(?P<slug>.+?)_(?P<year>\d{4})(?P<quarter>Q[1-4])?_(?P<suffix>10K|10Q|8K)(?:_.*)?$")
_SUFFIX_TO_FILING_TYPE = {"10K": "10-K", "10Q": "10-Q", "8K": "8-K"}


def parse_doc_name(doc_name: str) -> dict[str, Any]:
    """Best-effort fields from a doc_name, e.g. "3M_2018_10K" ->
    {"fiscal_year": 2018, "filing_type": "10-K"}. 12 of 84 names (EARNINGS
    releases, dated 8-Ks) don't match cleanly — known gap, callers handle missing keys."""
    match = _DOC_NAME_RE.match(doc_name)
    if not match:
        return {}
    result: dict[str, Any] = {
        "fiscal_year": int(match.group("year")),
        "filing_type": _SUFFIX_TO_FILING_TYPE[match.group("suffix")],
    }
    if match.group("quarter"):
        result["quarter"] = int(match.group("quarter")[1])
    return result
