"""FFF rewriter.

LLM output plus a mandatory deterministic validator. Rewrites reranked
propositions into Fully-Formatted Facts, one atomic fact per record.

C1 has no query-splitter structure, so `entity` is just the raw question
text and gets passed through as context instead of a resolved triple.

`subject` is always set from `source_prop.metadata.company` in code, never
trusted from the model — a C1 batch can span more than one company, and
letting the model restate the subject reopens the cross-entity blending
this call exists to prevent.

`subject_override` (C3/C4 only) pins every accepted FFF's subject to the
sub-query's placeholder token instead, so a real company name never leaks
back into the NPCE-on path right before generator/CoVe see it.

Numeric-verbatim guard: every numeric token in `value_text` must appear in
the specific proposition it claims, checked per-fact so a number can't be
borrowed from a neighbor. Failing facts are dropped, never repaired; an
all-dropped non-blank response gets retried up to `max_attempts` times.

Period-verbatim guard: same principle, added after a real CoVe wave-through
traced back to this call site relabeling "as of Dec 31, 2022" as FY2023 to
match the question. Rejects only on confirmed disagreement, never a guess.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Sequence

from contracts.models import FFF, Proposition, SourceRef
from llm.client import LLMClient
from online.curation.numeric_guard import validate_numeric_verbatim, validate_period_verbatim

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You rewrite retrieved financial passages into Fully-Formatted Facts.

A Fully-Formatted Fact is one atomic statement that:
  - concerns exactly one entity, one metric, and one period
  - restates its subject noun explicitly (never "it", "the company", "that year")
  - carries its value exactly as written in the source

RULES
- Copy every number, unit, currency, and date EXACTLY. Never round, convert scale,
  recompute, or normalise a figure.
- Emit only facts concerning the requested entity. Discard everything about any
  other entity, even if it appears in the same passage. This is the point of the
  task: preventing two similar entities from reaching the model together.
- Do not infer. If the passage does not state a value, do not produce a fact for it.
- If no proposition concerns the requested entity, return an empty list. An empty
  list is a correct answer.
"""


def _fff_id(proposition_id: str, metric: str, period_label: str, value_text: str) -> str:
    """Deterministic and stable for the same input, no counter, no randomness."""
    key = f"{proposition_id}|{metric}|{period_label}|{value_text}"
    return "f_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _source_ref(prop: Proposition) -> SourceRef:
    return SourceRef(filing_type=prop.metadata.filing_type, filing_date=prop.metadata.filing_date, page=prop.metadata.page)


def _rewrite_once(
    question: str,
    propositions: Sequence[Proposition],
    llm_client: LLMClient,
) -> list[dict[str, Any]]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "sub_query": {"question": question},
        "propositions": [
            {
                "proposition_id": p.proposition_id,
                "text": p.text,
                "source_ref": {
                    "filing_type": p.metadata.filing_type,
                    "filing_date": p.metadata.filing_date,
                    "page": p.metadata.page,
                },
            }
            for p in propositions
        ],
    }
    response = llm_client.complete(payload)
    return response.get("facts", [])


def rewrite_to_ffts(
    question: str,
    propositions: Sequence[Proposition],
    llm_client: LLMClient,
    max_attempts: int = 2,
    subject_override: str | None = None,
) -> list[FFF]:
    """Returns accepted FFFs; guard failures are dropped, never raised."""
    if not propositions:
        return []

    by_id = {p.proposition_id: p for p in propositions}
    accepted: list[FFF] = []

    for _ in range(max_attempts):
        raw_facts = _rewrite_once(question, propositions, llm_client)
        accepted = []
        for raw in raw_facts:
            proposition_id = raw.get("proposition_id")
            source_prop = by_id.get(proposition_id)
            if source_prop is None:
                continue  # references a proposition we never supplied
            value_text = raw.get("value_text", "")
            if not validate_numeric_verbatim(value_text, source_prop.text):
                continue  # numeric guard failed, reject not repair
            period_label = raw.get("period_label", "")
            if not validate_period_verbatim(period_label, source_prop.text):
                continue  # period guard failed, reject not repair
            accepted.append(
                FFF(
                    fff_id=_fff_id(proposition_id, raw.get("metric", ""), period_label, value_text),
                    proposition_id=proposition_id,
                    subject=subject_override if subject_override is not None else source_prop.metadata.company,
                    metric=raw.get("metric", ""),
                    period_label=period_label,
                    value_text=value_text,
                    provenance_tier=source_prop.provenance_tier,
                    source_ref=_source_ref(source_prop),
                )
            )
        if accepted or not raw_facts:
            # got something usable, or model correctly said "nothing here" --
            # only retry the everything-got-guard-rejected case
            break

    return accepted
