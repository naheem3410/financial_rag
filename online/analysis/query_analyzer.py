"""Query analyzer — noun-phrase collision detection.

Lexicon-only, plus the placeholder registry's pre-computed collision pairs
(`offline/registry/placeholder_builder.find_collision_candidates`) — this
module never re-embeds anything itself and makes no LLM calls. Queries the
lexicon can't parse return empty collision metadata and proceed.

Scope, stated precisely: this component detects collisions; it doesn't
drive splitting. Slot parsing for the split lives in the query splitter,
and the split is a Cartesian product over those slots, independent of
anything detected here — see `online/analysis/query_splitter.py`, which
never imports this module. A lexicon miss here degrades diagnostic
labelling only, never decomposition correctness.

Output: detected collision pairs, tagged by axis
(`contracts.models.CollisionTag`) — emitted into
`RequestContext.collision_tags` via `record_collision_tags` below, so
results can be reported per axis against FinanceBench-Collision without
re-deriving detection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from contracts.models import CollisionTag, PlaceholderRegistryEntry, RequestContext

# Metric-axis lexicon: gross/operating/net margin; revenue/net revenue;
# GAAP/non-GAAP; basic/diluted EPS. Extend as new surface forms are
# observed. Longest-phrase-first matching avoids "margin" partially
# matching inside "operating margin".
METRIC_SYNONYMS: dict[str, str] = {
    "gross margin": "gross_margin",
    "operating margin": "operating_margin",
    "net profit margin": "net_profit_margin",
    "net margin": "net_profit_margin",
    "net revenue": "net_revenue",
    "revenue": "revenue",
    "basic eps": "basic_eps",
    "diluted eps": "diluted_eps",
    "non-gaap": "non_gaap",
    "non gaap": "non_gaap",
    "gaap": "gaap",
}

METRIC_COLLISION_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"gross_margin", "operating_margin", "net_profit_margin"}),
    frozenset({"revenue", "net_revenue"}),
    frozenset({"gaap", "non_gaap"}),
    frozenset({"basic_eps", "diluted_eps"}),
)

_QUARTER_FY_RE = re.compile(r"\bQ(?P<quarter>[1-4])\s*FY\s*(?P<year>\d{2,4})\b", re.IGNORECASE)
_FY_ONLY_RE = re.compile(r"\bFY\s*(?P<year>\d{2,4})\b", re.IGNORECASE)


@dataclass(frozen=True)
class QueryPeriod:
    fiscal_year: int
    quarter: Optional[int] = None

    def label(self) -> str:
        return f"Q{self.quarter} FY{self.fiscal_year}" if self.quarter else f"FY{self.fiscal_year}"


@dataclass(frozen=True)
class QueryAnalysis:
    entities: tuple[str, ...] = ()  # matched placeholders, e.g. "<ENT_1>"
    metrics: tuple[str, ...] = ()  # canonical metric ids
    periods: tuple[QueryPeriod, ...] = ()
    collisions: tuple[CollisionTag, ...] = ()


def _normalize_year(raw: str) -> int:
    year = int(raw)
    return 2000 + year if year < 100 else year


def extract_metrics(query: str) -> tuple[str, ...]:
    lowered = query.lower()
    found: list[str] = []
    for phrase, canonical in sorted(METRIC_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
        if phrase in lowered and canonical not in found:
            found.append(canonical)
    return tuple(found)


def extract_periods(query: str) -> tuple[QueryPeriod, ...]:
    periods: list[QueryPeriod] = []
    seen: set[QueryPeriod] = set()

    for match in _QUARTER_FY_RE.finditer(query):
        period = QueryPeriod(fiscal_year=_normalize_year(match.group("year")), quarter=int(match.group("quarter")))
        if period not in seen:
            seen.add(period)
            periods.append(period)

    consumed_years = {p.fiscal_year for p in periods}
    for match in _FY_ONLY_RE.finditer(query):
        year = _normalize_year(match.group("year"))
        if year in consumed_years:
            continue  # already captured with an explicit quarter
        period = QueryPeriod(fiscal_year=year, quarter=None)
        if period not in seen:
            seen.add(period)
            periods.append(period)

    return tuple(periods)


def extract_entities(query: str, registry_entries: Sequence[PlaceholderRegistryEntry]) -> tuple[str, ...]:
    lowered = query.lower()
    found: list[str] = []
    for entry in registry_entries:
        if entry.surface_form.lower() in lowered and entry.placeholder not in found:
            found.append(entry.placeholder)
    return tuple(found)


def _metric_collisions(metrics: Sequence[str]) -> tuple[CollisionTag, ...]:
    metric_set = set(metrics)
    return tuple(
        CollisionTag(axis="metric", members=tuple(sorted(group & metric_set)))
        for group in METRIC_COLLISION_GROUPS
        if len(group & metric_set) >= 2
    )


def _temporal_collision(periods: Sequence[QueryPeriod]) -> tuple[CollisionTag, ...]:
    if len(periods) < 2:
        return ()
    return (CollisionTag(axis="temporal", members=tuple(p.label() for p in periods)),)


def _entity_collisions(
    entities: Sequence[str], entity_collision_pairs: Sequence[tuple[str, str]]
) -> tuple[CollisionTag, ...]:
    entity_set = set(entities)
    return tuple(
        CollisionTag(axis="entity", members=pair) for pair in entity_collision_pairs if set(pair) <= entity_set
    )


def analyze_query(
    query: str,
    registry_entries: Sequence[PlaceholderRegistryEntry] = (),
    entity_collision_pairs: Sequence[tuple[str, str]] = (),
) -> QueryAnalysis:
    """`entity_collision_pairs` is the pre-computed output of
    `find_collision_candidates` — passed in, never recomputed here."""
    metrics = extract_metrics(query)
    periods = extract_periods(query)
    entities = extract_entities(query, registry_entries)

    collisions = (
        _metric_collisions(metrics)
        + _temporal_collision(periods)
        + _entity_collisions(entities, entity_collision_pairs)
    )

    return QueryAnalysis(entities=entities, metrics=metrics, periods=periods, collisions=collisions)


def record_collision_tags(analysis: QueryAnalysis, ctx: RequestContext) -> None:
    """Emits the detected collisions into the per-request context so
    results can be reported per axis without re-deriving detection. `ctx`
    is mutated in place — `RequestContext` is per-request state threaded
    through the pipeline, not a value returned and reassigned."""
    ctx.collision_tags.extend(analysis.collisions)
