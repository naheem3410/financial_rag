"""Query splitter.

LLM for slot parsing only. The actual split (entities x metrics x periods)
is a deterministic Cartesian product in code, so completeness is provable
rather than trusted to a model's "split this query" judgment.

`PassthroughQueryTransformer` is the NPCE-off identity: one sub-query, no
split, no LLM. `LLMQuerySplitter` falls back to it on empty entities/metrics.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from contracts.models import Period, RequestContext, SubQuery
from llm.client import LLMClient

# Plain functions, not a Redis/MongoDB client directly -- keeps this module
# decoupled from infra and easy to fake in tests. Real callers bind these
# to infra.dictionary.lookup_metric / log_unmatched_phrase (step 2.2).
DictionaryLookup = Callable[[str], Optional[str]]
UnmatchedLogger = Callable[[str], None]

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
    # from financebench_collision_metric_axis.jsonl's own metric field --
    # without these the split silently falls back to NPCE-off behavior.
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
        return ""  # sentinel for "no period mentioned"
    if period.quarter:
        return f"Q{period.quarter} FY{period.fiscal_year}"
    return f"FY{period.fiscal_year}"


def render_subquery_question(sub_query: SubQuery) -> str:
    """Renders a sub-query's structured slots back into the NL question
    string the FFF rewriter/generator expect. By C3's pipeline, `entity`
    has already been placeholder-remapped, so this renders "<ENT_1>", never
    the real name."""
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
                period=Period(fiscal_year=0),  # unused in passthrough mode
                period_label="",
            )
        ]


class LLMQuerySplitter:
    """NPCE-on."""

    def __init__(
        self,
        llm_client: LLMClient,
        metric_lexicon: Sequence[str] = DEFAULT_METRIC_LEXICON,
        dictionary_lookup: Optional[DictionaryLookup] = None,
        unmatched_logger: Optional[UnmatchedLogger] = None,
    ):
        self._llm_client = llm_client
        self._metric_lexicon = list(metric_lexicon)
        self._dictionary_lookup = dictionary_lookup
        self._unmatched_logger = unmatched_logger

    def transform(self, query: str, ctx: RequestContext) -> Sequence[SubQuery]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "system_prompt": SYSTEM_PROMPT,
            "query": query,
            "metric_lexicon": self._metric_lexicon,
        }
        response: dict[str, Any] = self._llm_client.complete(payload)

        entities = response.get("entities") or []
        metrics = list(response.get("metrics") or [])
        self._resolve_unmapped_metrics(response.get("unmapped_metrics") or [], metrics)

        if not entities or not metrics:
            return PassthroughQueryTransformer().transform(query, ctx)

        periods_raw = response.get("periods") or []
        periods = [
            Period(fiscal_year=p["fiscal_year"], quarter=p.get("quarter")) for p in periods_raw
        ] or [Period(fiscal_year=0)]  # no period -> one unconstrained slot

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

    def _resolve_unmapped_metrics(self, unmapped: Sequence[str], metrics: list[str]) -> None:
        """Step 2.2: a phrase the model couldn't match against the fixed
        lexicon gets one more chance against the free-text dictionary
        before being given up on. Resolved phrases join `metrics` in place;
        genuinely unknown ones are logged so the dictionary can grow from
        them later (decision 9) -- never silently dropped either way."""
        for phrase in unmapped:
            resolved = self._dictionary_lookup(phrase) if self._dictionary_lookup else None
            if resolved:
                if resolved not in metrics:
                    metrics.append(resolved)
            elif self._unmatched_logger:
                self._unmatched_logger(phrase)
