"""Draft assembler. Pure templating, no model involved.

Combines each sub-query's `GeneratedAnswer` into one draft D, a plain
per-sub-query listing. No LLM here -- the assembler sits before CoVe, and
CoVe verifies claims against evidence, not "did assembly render faithfully."

A sub-query with no answerable facts gets an explicit "not provided" line
instead of being dropped, so a partial-retrieval crowd-out stays visible
in the draft rather than silently disappearing.
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
    """`{a_1..a_n} -> D`. One line per value; nothing answerable gets an
    explicit "not provided" line instead of being omitted."""
    lines: list[str] = []
    for sub_answer in sub_answers:
        label = _label(sub_answer.sub_query)
        generated = sub_answer.generated
        if not generated.answerable or not generated.answers:
            lines.append(f"{label}: not provided.")
            continue
        for answer_value in generated.answers:
            lines.append(f"{label}: {_value_with_unit(answer_value.value, answer_value.unit)}")
    return "\n".join(lines)


def _value_with_unit(value: str, unit: str) -> str:
    """Skip the unit if the value already ends with it, so we never print
    "2,266 million million"."""
    value = value.rstrip()
    if not unit or value.lower().endswith(unit.strip().lower()):
        return value
    return f"{value} {unit}"
