"""Query splitter.

LLM for slot parsing only. The split itself — `entities × metrics ×
periods` — is a deterministic Cartesian product in Python: asking an LLM to
"split this query" is unreliable and unprovable, whereas deriving
`{2 metrics} × {2 periods} = 4` sub-queries in code makes the completeness
of the split provable by construction. Each emitted sub-query concerns
exactly one entity, one metric, one period — no collision pair co-occurs
in any sub-query, by construction of the nested loop below.

`PassthroughQueryTransformer` is the NPCE-off implementation: the identity
— one sub-query, no split, no LLM call. `LLMQuerySplitter` also falls back
to it when the model returns empty `entities` or `metrics`.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from contracts.models import Period, RequestContext, SubQuery
from llm.client import LLMClient

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You parse a financial question into structured slots.

Extract every entity (company), every metric, and every period the question refers
to. List each one separately, even when the question phrases them together — "operating
and net margin" is two metrics; "Q3 and Q4" is two periods.

Use the canonical metric identifiers from the provided lexicon. If a metric in the
question is not in the lexicon, return its surface form in `unmapped_metrics` rather
than forcing a match.

Do not answer the question. Do not decompose it. Do not add entities, metrics, or
periods that the question does not mention.
"""

DEFAULT_METRIC_LEXICON: tuple[str, ...] = (
    "operating_margin",
    "net_profit_margin",
    "gross_margin",
    "revenue",
    "net_revenue",
    "basic_eps",
    "diluted_eps",
    "gaap",
    "non_gaap",
    # The FinanceBench-Collision metric-axis dataset's own metric
    # identifiers (evaluation/data/financebench_collision_metric_axis.jsonl's
    # `metrics` field). Without these, a question using one of them falls
    # into `unmapped_metrics` and the split silently falls back to
    # PassthroughQueryTransformer (empty `metrics` -> NPCE-off behavior).
    "accounts_payable",
    "accounts_receivable",
    "capital_expenditure",
    "gross_profit",
    "inventories",
    "net_income",
    "operating_income",
    "total_current_assets",
    "total_current_liabilities",
    "total_revenue",
)


def _render_period_label(period: Period) -> str:
    if period.fiscal_year == 0:
        return ""  # sentinel for "no period mentioned" — see LLMQuerySplitter
    if period.quarter:
        return f"Q{period.quarter} FY{period.fiscal_year}"
    return f"FY{period.fiscal_year}"


def render_subquery_question(sub_query: SubQuery) -> str:
    """Renders one atomic, single-entity/metric/period sub-query back into
    the natural-language question string the FFF rewriter and generator
    prompts expect — `SubQuery` itself only carries structured slots, no NL
    string. Uses `sub_query.entity` verbatim, whatever it currently holds —
    by the time C3's pipeline calls this, that has already been through the
    query-side placeholder remapper, so this renders `"<ENT_1>"`, never the
    real company name, keeping the rendered prompt self-consistent with the
    FFF's own `subject` (`online/curation/fff_rewriter.py`'s
    `subject_override`)."""
    metric_phrase = sub_query.metric.replace("_", " ")
    period_phrase = f" for {sub_query.period_label}" if sub_query.period_label else ""
    return f"What was {sub_query.entity}'s {metric_phrase}{period_phrase}?"


class PassthroughQueryTransformer:
    """NPCE-off: the identity. One sub-query, no split, no LLM call."""

    def transform(self, query: str, ctx: RequestContext) -> Sequence[SubQuery]:
        return [
            SubQuery(
                sub_query_id="sq_1",
                entity=query,
                metric="",
                period=Period(fiscal_year=0),  # unused sentinel in passthrough mode
                period_label="",
            )
        ]


class LLMQuerySplitter:
    """NPCE-on."""

    def __init__(self, llm_client: LLMClient, metric_lexicon: Sequence[str] = DEFAULT_METRIC_LEXICON):
        self._llm_client = llm_client
        self._metric_lexicon = list(metric_lexicon)

    def transform(self, query: str, ctx: RequestContext) -> Sequence[SubQuery]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "system_prompt": SYSTEM_PROMPT,
            "query": query,
            "metric_lexicon": self._metric_lexicon,
        }
        response: dict[str, Any] = self._llm_client.complete(payload)

        entities = response.get("entities") or []
        metrics = response.get("metrics") or []
        # unmapped_metrics is logged for lexicon expansion, not a failure —
        # proceed with whatever mapped.

        if not entities or not metrics:
            return PassthroughQueryTransformer().transform(query, ctx)

        periods_raw = response.get("periods") or []
        periods = [
            Period(fiscal_year=p["fiscal_year"], quarter=p.get("quarter")) for p in periods_raw
        ] or [Period(fiscal_year=0)]  # no period mentioned -> one unconstrained slot

        sub_queries: list[SubQuery] = []
        counter = 1
        for entity in entities:
            for metric in metrics:
                for period in periods:
                    sub_queries.append(
                        SubQuery(
                            sub_query_id=f"sq_{counter}",
                            entity=entity,
                            metric=metric,
                            period=period,
                            period_label=_render_period_label(period),
                        )
                    )
                    counter += 1
        return sub_queries
