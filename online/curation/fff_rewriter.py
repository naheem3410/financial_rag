"""FFF rewriter.

LLM structured output plus a mandatory deterministic validator. Rewrites
reranked propositions into Fully-Formatted Facts — one atomic fact per
record, exactly one entity/metric/period each.

C1 (NPCE-off) adaptation: the NPCE-on case gives this call a resolved
company/metric/period triple, already split out by the query splitter.
C1's `PassthroughQueryTransformer` sub-query has no such structure
(`entity` holds the raw question text); this module passes the question
through as context instead.

`subject` is constructed deterministically, not taken from model output —
the same principle already applied to `source_ref`. C1's retrieval has no
metadata-filter constraint (it's a documented no-op under passthrough —
`online/retrieval/metadata_filter.py`), so a batch of reranked propositions
handed to this call can legitimately span more than one company. Trusting
the model to correctly restate which company a fact belongs to reintroduces
exactly the cross-entity blending risk this call site exists to guard
against. Instead, `subject` in every accepted `FFF` is always set to the
linked proposition's own `metadata.company` by default — authoritative,
from code, regardless of what the model wrote in that field. The model
still emits a `subject` (the schema requires it, and it plausibly helps the
model's own reasoning quality), but it's never trusted.

`subject_override` is for C3/C4 (NPCE-on): under NPCE, every proposition
handed to one call here already belongs to exactly one sub-query's one
resolved entity (the metadata filter already scoped retrieval to that
placeholder), so the correct subject is the sub-query's placeholder token,
not the real company name — putting the real name back into the FFF would
leak exactly the noun-phrase substitution the placeholder system exists to
remove, right before the generator/CoVe calls. Passing `subject_override`
sets every accepted FFF's `subject` to that fixed string instead of
`source_prop.metadata.company`; omitting it (the C1/C2 default) leaves the
original, tested behavior unchanged.

Numeric-verbatim guard (mandatory): every numeric token in `value_text`
must appear verbatim in the specific proposition `proposition_id` points at
(online/curation/numeric_guard.py) — checked per-fact against that
proposition's own text, not the whole batch, so a fact can't borrow a
number from a neighboring proposition. `proposition_id` must also
reference one of the propositions actually supplied. Facts failing either
check are dropped, never repaired. If every fact in a response is dropped
(or the response is empty) for non-blank input, the whole call is retried
up to `max_attempts` times, matching
`offline/propositions/segmenter_llm.py`'s established retry-then-drop
pattern — never a per-fact "ask again" retry, which a stateless
request/response call can't cleanly express anyway.

Period-verbatim guard (mandatory): the same principle, extended from
values to periods, after tracing a real CoVe "wave-through" to its true
root cause: this call site relabeled a proposition genuinely stated "as of
December 31, 2022" with `period_label: "FY2023"` — matching the question
rather than the source. A fact's period is as much a fact as its value,
and only the value was previously protected. Checked per-fact against that
proposition's own text, same as the numeric guard; rejects only on a
confirmed disagreement (see `validate_period_verbatim`'s docstring for why
an unparseable period on either side passes rather than guesses).
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
    """Deterministic and stable given the same input — no randomness, no
    incrementing global counter."""
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
    """Returns accepted `FFF`s — never raises on a guard failure, drops
    instead."""
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
                continue  # references a proposition never supplied -- reject
            value_text = raw.get("value_text", "")
            if not validate_numeric_verbatim(value_text, source_prop.text):
                continue  # numeric-verbatim guard -- reject, never repair
            period_label = raw.get("period_label", "")
            if not validate_period_verbatim(period_label, source_prop.text):
                continue  # period-verbatim guard -- reject, never repair
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
            # Either got something usable, or the model correctly reported
            # "no facts concern this entity" (an empty list is a valid,
            # non-retriable answer per the system prompt) -- either way,
            # not the "everything got guard-rejected" case worth retrying.
            break

    return accepted
