"""Several versions of the same fact (decision 11; step 4.1's own test
table row, "top results almost the same, differ in one detail -> ANSWER
EACH, labelled"). No model call: a genuine collision here is a property of
the retrieved evidence itself (two different numbers for the same period),
checked deterministically, the same "code decides, model never guesses"
rule the numeric-verbatim guard already follows elsewhere.

Scoped to `basis` (reported / restated / ttm) and, since decision 21's
metric-granularity fix, to overlapping metric wording within the same
period and basis -- decision 4's "unit fix" already normalizes scale and
currency elsewhere in the pipeline, so a scale or currency mismatch here
would be a data bug to fix upstream, not a legitimate second version of a
fact the way a genuine restatement or a genuinely ambiguous line item is.

Decision 21's second fix (metric-granularity ambiguity, e.g. PepsiCo "net
income" vs "net income attributable to PepsiCo", or General Mills "total
receivables" vs "customers receivables"): reuses this module's own
`detect_near_identical_versions`/`answer_each_version` pair rather than a
parallel implementation, because every downstream consumer -- the
`pipeline_c4.py` call site, the "answer each version, labelled" generation
path, and the `near_identical_detected`/`near_identical_detail` fields
already reported to the caller -- is already generic over "a collision was
found in this group, here are its labelled versions" and needed no change
at all. The only new code is a second, narrower detection branch tried
when the basis check doesn't fire, and a deterministic label derived from
each proposition's own text (never an LLM guess, same "code decides" rule
as the basis check).

The metric-overlap signal is `Proposition.metrics` -- the canonical metric
id(s) a proposition was already labelled with by `offline/labeling/metric_labeler.py`.
Two propositions "overlap" if they share at least one canonical id (e.g.
both tagged `accounts_receivable`), which is exactly what the query
splitter's own metric slot would have resolved them under, with the
numeric value still genuinely different. [Judgment call, flagged for the
owner]: this fires on any canonical-id overlap with a differing value; it
does not try to tell "genuinely different sub-component" apart from
"OCR/extraction noise producing a slightly different number for what
should be the same fact" -- too aggressive a value-difference threshold
would start fragmenting answers that were actually fine, too lax would
miss real ambiguity, and this module has no basis in the existing basis
check (which uses an exact float inequality, not a tolerance) to pick one
over the other. Left at exact-value inequality, matching the basis check's
own strictness, until the owner says otherwise.

Decision 30: the metric-wording branch is now also restricted to the top 2
of the reranked pool by rank, not any match anywhere in the up-to-10
candidates -- real pilot data showed it firing on a lower-ranked, unrelated
same-labelled mention and fragmenting an otherwise-correct top-ranked
answer. The basis check keeps its original, unrestricted scope.

This closes a real gap: this road was named in the plan's own diagram and
its own test row but was never actually built across steps 4.1-4.6 (see
online/routing/how_it_happened.py's note before this was added, and
KNOWN_ISSUES.md's 2026-09-27 entry). Built once the owner confirmed it
should be built now, not deferred.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional, Sequence

from contracts.models import FFF, Proposition, SubQuery
from contracts.numeric import value_in_base_units
from llm.client import LLMClient
from online.curation.fff_rewriter import rewrite_to_ffts
from online.generation.assembler import SubAnswer
from online.output.generator import AnswerValue, GeneratedAnswer, generate_answer

_BASIS_LABELS = {"reported": "originally reported", "restated": "restated", "ttm": "trailing twelve months"}

# Deterministic qualifier scan for the metric-wording branch below -- order
# matters, first match wins, so put the more specific phrases first.
_QUALIFIER_PHRASES = (
    "attributable to", "including noncontrolling interests", "noncontrolling interests",
    "continuing operations", "customers", "customer", "trade", "total", "net", "gross",
    "basic", "diluted",
)


@dataclass(frozen=True)
class NearIdenticalCollision:
    differing_detail: str  # "basis", or "metric_wording" (decision 21's granularity fix)
    # (plain label, the propositions carrying that version), stable order
    groups: tuple[tuple[str, tuple[Proposition, ...]], ...]


def _group_by_period(props: Sequence[Proposition]) -> dict[tuple[int, Optional[int]], list[Proposition]]:
    numeric_props = [p for p in props if p.numeric is not None]
    by_period: dict[tuple[int, Optional[int]], list[Proposition]] = {}
    for p in numeric_props:
        assert p.numeric is not None  # narrows for the type checker; filtered above
        key = (p.numeric.period.fiscal_year, p.numeric.period.quarter)
        by_period.setdefault(key, []).append(p)
    return by_period


def _qualifier_label(text: str) -> str:
    """Best-effort label pulled from the proposition's own wording, never
    an LLM guess. Falls back to a generic label so the ambiguity still
    surfaces even when no known qualifier phrase is present."""
    lowered = text.lower()
    for phrase in _QUALIFIER_PHRASES:
        if phrase in lowered:
            return phrase
    return "unlabelled variant"


def _metric_wording_collision(
    group: Sequence[Proposition], top_ranked_ids: frozenset[str]
) -> Optional[NearIdenticalCollision]:
    """Decision 21's granularity fix: same period, same (or unmentioned)
    basis, but two propositions share a canonical metric id yet carry
    genuinely different values -- e.g. "total receivables" vs "customers
    receivables" both tagged `accounts_receivable`. Flags it the same way
    the basis check flags reported-vs-restated, instead of the generator
    silently picking whichever one retrieval happened to rank first.

    Decision 30: only a real tie for the top of the reranked evidence --
    both propositions within the top 2 by cross-encoder score -- counts as
    genuine ambiguity. A same-labelled mention further down the ranked
    pool is noise (confirmed on real pilot data: Adobe/American Water
    Works/Boeing-Lockheed all had their correct, top-ranked answer
    fragmented against an unrelated lower-ranked mention), not a case
    where two facts are genuinely competing for the answer."""
    labelled = [p for p in group if p.metrics and p.proposition_id in top_ranked_ids]
    if len(labelled) < 2:
        return None
    overlap = any(
        set(p1.metrics) & set(p2.metrics)
        for i, p1 in enumerate(labelled) for p2 in labelled[i + 1:]
    )
    if not overlap:
        return None
    by_value: dict[Decimal, list[Proposition]] = {}
    for p in labelled:
        assert p.numeric is not None
        by_value.setdefault(value_in_base_units(p.numeric.value, p.numeric.scale), []).append(p)
    if len(by_value) < 2:
        return None

    labels_used: set[str] = set()
    version_groups: list[tuple[str, tuple[Proposition, ...]]] = []
    for value in sorted(by_value):
        props_for_value = by_value[value]
        label = _qualifier_label(props_for_value[0].text)
        candidate, n = label, 2
        while candidate in labels_used:
            candidate = f"{label} ({n})"
            n += 1
        labels_used.add(candidate)
        version_groups.append((candidate, tuple(props_for_value)))
    return NearIdenticalCollision(differing_detail="metric_wording", groups=tuple(version_groups))


def detect_near_identical_versions(
    props: Sequence[Proposition],
    logits: Optional[Mapping[str, float]] = None,
    tie_margin: Optional[float] = None,
) -> Optional[NearIdenticalCollision]:
    """Looks at the top propositions actually retrieved for one sub-query.
    A real collision needs BOTH a different basis and a different value
    for the same period -- two facts that happen to agree, or that merely
    share a period with something unrelated, are not a collision to flag.
    Falling that, tries the metric-wording check (decision 21): same
    period, overlapping canonical metric id, genuinely different value --
    restricted (decision 30) to the top 2 of `props` by rank, since `props`
    is the reranker's own output, best match first (confirmed in
    `online/retrieval/reranker.py::CrossEncoderReranker.rerank`, which
    sorts by score descending before truncating to k). The basis check
    just above is untouched -- no real evidence found that it over-fires
    the same way.

    Decision 34: the top-2 window is still the reranker's own global rank
    order, as decision 30 set it -- NOT taken per period (tried that first,
    reverted: it broke Boeing, see below). It now first drops any candidate
    whose own (fiscal_year, quarter) key has no second member anywhere in
    `props` -- a period with only one candidate can never pass the
    `len(group) < 2` guard a few lines down, so it could never be part of a
    collision regardless of rank. Letting such a candidate occupy one of
    only two window slots was pure waste: on AES's real data, a cash-flow
    "increase in accounts payable" line (quarter=None, no other candidate
    shares that key) sat at rank 0 or 1 and crowded out one of the two real,
    same-period ($1,730M vs $33M) candidates every time, so they were never
    both in the window together. Dropping the friendless candidate first
    fixes that without widening the window -- confirmed against real AES
    data in both observed rerank orderings.

    Re-localizing the window per period (take `group[:2]` of each period's
    own slice) was the first attempt and looked right on paper, but broke
    Boeing's own confirmed-correct case (decision 33): Boeing's real
    headline balance and two cash-flow/footnote lines about the SAME annual
    filing carry inconsistent quarter tags (quarter=4 for the balance,
    quarter=None for the flow lines) -- `metadata_filter.py`'s own period
    query already treats an unspecified quarter as "match any" for exactly
    this reason, but `_group_by_period`'s literal tuple key does not, so it
    splits one real annual figure into two "periods." Per-group top-2 let
    each of those two slices separately qualify as "top 2 of itself" even
    though their members are not globally near the top, firing a false
    collision on two low-ranked decoys before the real headline's own group
    was ever reached. Dropping only genuinely friendless (singleton-period)
    candidates from the GLOBAL window avoids that: Boeing's periods both
    have 2+ members, so nothing is dropped and the window is unchanged from
    decision 30/33's behavior -- confirmed this restores Boeing's
    `exact_match: True` while keeping AES fixed."""
    by_period = _group_by_period(props)
    eligible_for_window = [
        p for p in props
        if p.numeric is not None and len(by_period[(p.numeric.period.fiscal_year, p.numeric.period.quarter)]) >= 2
    ]
    window = eligible_for_window[:2]
    if tie_margin is not None and logits is not None and len(window) == 2:
        first, second = (logits.get(p.proposition_id) for p in window)
        # A second fact far below the first in reranker score is not a real rival.
        if first is not None and second is not None and first - second > tie_margin:
            window = window[:1]
    top_ranked_ids = frozenset(p.proposition_id for p in window)

    for key in sorted(by_period, key=lambda k: (k[0], k[1] or 0)):
        group = by_period[key]
        if len(group) < 2:
            continue
        bases = {p.numeric.period.basis for p in group if p.numeric}
        values = {value_in_base_units(p.numeric.value, p.numeric.scale) for p in group if p.numeric}
        if len(bases) >= 2 and len(values) >= 2:
            by_basis: dict[str, list[Proposition]] = {}
            for p in group:
                assert p.numeric is not None
                by_basis.setdefault(p.numeric.period.basis, []).append(p)
            groups = tuple(
                (_BASIS_LABELS.get(basis, basis), tuple(basis_props))
                for basis, basis_props in sorted(by_basis.items())
            )
            return NearIdenticalCollision(differing_detail="basis", groups=groups)

        wording_collision = _metric_wording_collision(group, top_ranked_ids)
        if wording_collision is not None:
            return wording_collision
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
