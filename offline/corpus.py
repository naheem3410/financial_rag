"""FinanceBench corpus scope — which documents to index.

Index only the documents referenced by the question set (84 of the full
corpus placed under `data/pdfs/`), not the whole directory: indexing the
rest is roughly 4x the LLM segmentation cost for documents no question asks
about, and it changes retrieval difficulty by adding distractors no
question needs.

`doc_name` (e.g. `"3M_2018_10K"`) is the join key between a FinanceBench
question and its source document — resolved to `doc_name + ".pdf"` under
the corpus directory. Never rename these files: doing so breaks the join
between gold evidence and propositions.

`data/required_docs.txt` is an independently-verified 84-name allowlist,
committed (not gitignored) as an assertion target. If the set derived from
the FinanceBench question set doesn't match it exactly, that means the join
is broken — `assert_matches_required_docs` raises rather than letting an
expensive indexing run proceed on the wrong document set.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Union


def required_doc_names(financebench_jsonl_path: Union[str, Path]) -> set[str]:
    """The `doc_name` field, deduplicated, across every question in the
    FinanceBench open-source release. This — not the full corpus directory
    — is what gets indexed.

    Every `evidence[].evidence_doc_name` in the dataset is already among
    these primary `doc_name`s (verified independently), so filtering on
    `doc_name` alone is complete — no separate pass over
    `evidence_doc_name` is needed.
    """
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
    """Metric-axis 5-document subset — a deliberate narrowing to the
    metric-collision axis for this experimental run, distinct from
    `required_doc_names`'s 84-document primary corpus.

    Reads `source.primary_doc` and `source.evidence_refs[].doc_name` from a
    FinanceBench-Collision jsonl (`evaluation/data/
    financebench_collision_metric_axis.jsonl` schema — distinct from the
    stock FinanceBench release's flat `doc_name` field read by
    `required_doc_names`). `evidence_refs[].doc_name` is nullable, meaning
    "same as `primary_doc`" — a null contributes no additional document.

    Does not read `gold_answers`: this file's gold answers are a
    user-maintained, partially-annotated field that must never be read,
    inferred, or filled by anything in this codebase.
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
    """Scope check: the derived `doc_name` set must exactly match
    `data/required_docs.txt`. A mismatch means the join between questions
    and documents may be broken — stop rather than run an expensive
    indexing pass on the wrong document set.
    """
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
    """`doc_name` -> the actual `.pdf` path under `pdf_dir`. Raises if any
    required document is missing from the corpus directory — better to fail
    loudly before an expensive indexing run than discover a gap mid-run.
    """
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
    """Best-effort structured fields from a FinanceBench `doc_name` — e.g.
    `"3M_2018_10K"` -> `{"fiscal_year": 2018, "filing_type": "10-K"}`.
    Returns whatever it can extract; callers must handle missing keys.

    12 of the 84 `doc_name`s (EARNINGS releases, and 8-Ks with a
    `_dated-YYYY-MM-DD` suffix) don't match this pattern, or match but have
    no clean `filing_type` mapping for "EARNINGS" — a known gap, not
    resolved here.
    """
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
