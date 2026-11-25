"""Fact-set/query pairer tests."""
from __future__ import annotations

from contracts.models import FFF, Period, RequestContext, SourceRef, SubQuery
from online.curation.pairer import pair_facts


def _sub_query(sub_query_id: str) -> SubQuery:
    return SubQuery(
        sub_query_id=sub_query_id, entity="<ENT_1>", metric="net_income",
        period=Period(fiscal_year=2022), period_label="FY2022",
    )


def _fff(fff_id: str) -> FFF:
    return FFF(
        fff_id=fff_id, proposition_id="p_1", subject="<ENT_1>", metric="net_income",
        period_label="FY2022", value_text="$5,777 million", provenance_tier="retrieved",
        source_ref=SourceRef(filing_type="10-K", filing_date="2022-12-31", page=47),
    )


def test_pairs_one_sub_query_own_facts_into_ctx():
    ctx = RequestContext(request_id="r_1", config_hash="cfg")
    sq = _sub_query("sq_1")
    ffts = [_fff("f_1"), _fff("f_2")]

    pair_facts(sq, ffts, ctx)

    assert ctx.fff_sets == {"sq_1": ffts}


def test_two_sub_queries_facts_never_mix():
    ctx = RequestContext(request_id="r_1", config_hash="cfg")
    pair_facts(_sub_query("sq_1"), [_fff("f_1")], ctx)
    pair_facts(_sub_query("sq_2"), [_fff("f_2")], ctx)

    assert set(ctx.fff_sets.keys()) == {"sq_1", "sq_2"}
    assert ctx.fff_sets["sq_1"] != ctx.fff_sets["sq_2"]


def test_empty_ffts_still_recorded_not_skipped():
    """A sub-query with zero accepted facts is a reportable outcome for
    the abstention trigger, not something to drop silently."""
    ctx = RequestContext(request_id="r_1", config_hash="cfg")
    pair_facts(_sub_query("sq_1"), [], ctx)

    assert ctx.fff_sets == {"sq_1": []}
