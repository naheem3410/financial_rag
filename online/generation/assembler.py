"""Draft assembler. Pure templating, no model involved.

Combines each sub-query's own `GeneratedAnswer` into one draft `D` — a
plain per-sub-query listing, nothing computed, nothing inferred. An LLM is
not used here: needing CoVe to catch assembler-introduced errors would be
worse than not introducing them, since the assembler sits before CoVe in
the pipeline and CoVe verifies claims against evidence, not against "did
the assembler render its own inputs faithfully."

A sub-query with no answerable facts is rendered as an explicit "not
provided" line, not silently dropped — this is what makes the
capacity/partial-drop mechanism visible in the draft itself: if NPCE's
per-metric retrieval budget genuinely fixes a crowd-out, both lines carry
real values; if one metric's fact still isn't retrievable, the draft says
so honestly, exactly like C1's single-sub-query behavior already does for
a partial answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from contracts.models import SubQuery
from online.output.generator import GeneratedAnswer


@dataclass(frozen=True)
class SubAnswer:
    sub_query: SubQuery
    generated: GeneratedAnswer


def _label(sub_query: SubQuery) -> str:
    metric_phrase = sub_query.metric.replace("_", " ")
    period_phrase = f" ({sub_query.period_label})" if sub_query.period_label else ""
    return f"{sub_query.entity} {metric_phrase}{period_phrase}"


def assemble_draft(sub_answers: Sequence[SubAnswer]) -> str:
    """`{a_1..a_n} -> D`. One line per reported value; a sub-query with
    nothing answerable gets one explicit "not provided" line instead of
    being omitted."""
    lines: list[str] = []
    for sub_answer in sub_answers:
        label = _label(sub_answer.sub_query)
        generated = sub_answer.generated
        if not generated.answerable or not generated.answers:
            lines.append(f"{label}: not provided.")
            continue
        for answer_value in generated.answers:
            lines.append(f"{label}: {answer_value.value} {answer_value.unit}".rstrip())
    return "\n".join(lines)
