"""Several versions of the same fact (decision 11; step 4.1's own test
table row, "top results almost the same, differ in one detail -> ANSWER
EACH, labelled"). No model call: a genuine collision here is a property of
the retrieved evidence itself (two different numbers for the same period),
checked deterministically, the same "code decides, model never guesses"
rule the numeric-verbatim guard already follows elsewhere.

Scoped to `basis` only (reported / restated / ttm) -- decision 4's "unit
fix" already normalizes scale and currency elsewhere in the pipeline, so a
scale or currency mismatch here would be a data bug to fix upstream, not a
legitimate second version of a fact the way a genuine restatement is.

This closes a real gap: this road was named in the plan's own diagram and
its own test row but was never actually built across steps 4.1-4.6 (see
online/routing/how_it_happened.py's note before this was added, and
KNOWN_ISSUES.md's 2026-09-27 entry). Built once the owner confirmed it
should be built now, not deferred.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from contracts.models import FFF, Proposition, SubQuery
from llm.client import LLMClient
from online.curation.fff_rewriter import rewrite_to_ffts
from online.generation.assembler import SubAnswer
from online.output.generator import AnswerValue, GeneratedAnswer, generate_answer

_BASIS_LABELS = {"reported": "originally reported", "restated": "restated", "ttm": "trailing twelve months"}


@dataclass(frozen=True)
class NearIdenticalCollision:
    differing_detail: str  # always "basis" today -- the one detail this checks
    # (plain label, the propositions carrying that version), stable order
    groups: tuple[tuple[str, tuple[Proposition, ...]], ...]


def detect_near_identical_versions(props: Sequence[Proposition]) -> Optional[NearIdenticalCollision]:
    """Looks at the top propositions actually retrieved for one sub-query.
    A real collision needs BOTH a different basis and a different value
    for the same period -- two facts that happen to agree, or that merely
    share a period with something unrelated, are not a collision to flag."""
    numeric_props = [p for p in props if p.numeric is not None]
    by_period: dict[tuple[int, Optional[int]], list[Proposition]] = {}
    for p in numeric_props:
        assert p.numeric is not None  # narrows for the type checker; filtered above
        key = (p.numeric.period.fiscal_year, p.numeric.period.quarter)
        by_period.setdefault(key, []).append(p)

    for key in sorted(by_period, key=lambda k: (k[0], k[1] or 0)):
        group = by_period[key]
        if len(group) < 2:
            continue
        bases = {p.numeric.period.basis for p in group if p.numeric}
        values = {p.numeric.value for p in group if p.numeric}
        if len(bases) < 2 or len(values) < 2:
            continue
        by_basis: dict[str, list[Proposition]] = {}
        for p in group:
            assert p.numeric is not None
            by_basis.setdefault(p.numeric.period.basis, []).append(p)
        groups = tuple(
            (_BASIS_LABELS.get(basis, basis), tuple(basis_props))
            for basis, basis_props in sorted(by_basis.items())
        )
        return NearIdenticalCollision(differing_detail="basis", groups=groups)
    return None


def answer_each_version(
    sub_query: SubQuery,
    rendered_question: str,
    collision: NearIdenticalCollision,
    fff_client: LLMClient,
    generator_client: LLMClient,
) -> list[tuple[SubAnswer, list[FFF]]]:
    """Runs the normal FFF-rewrite + generate pass once per version in the
    collision, each seeing only its own version's evidence -- the model
    never sees both at once, so it can never blend or silently prefer one.
    Every resulting SubAnswer/AnswerValue has its metric labelled with
    which version it is, for the draft, the citations, and "How it
    happened" (step 4.7) alike. Each SubAnswer is paired with only its OWN
    version's FFFs (never the other version's) so a caller's `pair_facts`
    (online/curation/pairer.py) call keys `ctx.fff_sets` correctly -- a
    distinct `sub_query_id` per version keeps the two from overwriting
    each other there too."""
    results: list[tuple[SubAnswer, list[FFF]]] = []
    for label, props in collision.groups:
        ffts = rewrite_to_ffts(rendered_question, props, fff_client, subject_override=sub_query.entity)
        generated = generate_answer(rendered_question, ffts, generator_client)
        suffix = f" ({label})"
        relabelled = GeneratedAnswer(
            answerable=generated.answerable,
            answers=[AnswerValue(metric=f"{a.metric}{suffix}", value=a.value, unit=a.unit) for a in generated.answers],
            answer_text=generated.answer_text,
            used_fff_ids=generated.used_fff_ids,
        )
        labelled_sub_query = sub_query.model_copy(update={
            "sub_query_id": f"{sub_query.sub_query_id}_{label.replace(' ', '_')}",
            "metric": f"{sub_query.metric}{suffix}" if sub_query.metric else label,
        })
        results.append((SubAnswer(sub_query=labelled_sub_query, generated=relabelled), ffts))
    return results
