"""Fact-set/query pairer. No model involved.

Binds one sub-query's own accepted FFFs into `RequestContext.fff_sets`,
keyed by `sub_query_id` — makes "sub-query s_i's generator call only ever
sees s_i's own facts" an explicit, testable data structure rather than an
implicit property of call ordering in the pipeline that assembles it.
"""
from __future__ import annotations

from typing import Sequence

from contracts.models import FFF, RequestContext, SubQuery


def pair_facts(sub_query: SubQuery, ffts: Sequence[FFF], ctx: RequestContext) -> None:
    """Mutates `ctx.fff_sets[sub_query.sub_query_id]` in place —
    `RequestContext` is per-request state, never shared across requests,
    so this mutation is safe."""
    ctx.fff_sets[sub_query.sub_query_id] = list(ffts)
