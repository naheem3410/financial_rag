"""Fact-set/query pairer. No model involved.

Binds one sub-query's accepted FFFs into `RequestContext.fff_sets` by
`sub_query_id`, so each sub-query's generator call only sees its own facts.
"""
from __future__ import annotations

from typing import Sequence

from contracts.models import FFF, RequestContext, SubQuery


def pair_facts(sub_query: SubQuery, ffts: Sequence[FFF], ctx: RequestContext) -> None:
    """Mutates `ctx.fff_sets` in place; safe since `ctx` is per-request state."""
    ctx.fff_sets[sub_query.sub_query_id] = list(ffts)
