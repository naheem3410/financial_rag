"""Labels a fact with which financial metric(s) it is about, by matching
known phrases in its text. Rules first, always -- a model is only asked
about a fact the rules found nothing at all for, and it may only pick from
the same known list, never invent a new metric name (the "classify, never
invent" rule offline/extraction/numeric_extractor.py's tier 3 already
follows). The owner's yes for this as its own call site (metric_label_fallback)
was given 2026-09-26.

This alias list is a first, deliberately small dictionary sized to the
366-item benchmark's own metrics. The fuller dictionary (many more sources,
free text, kept in MongoDB) is work package 2.2 -- this module's shape
(text in, metric labels out) is the seam that swaps in cleanly.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Protocol, Sequence

from contracts.models import Proposition
from llm.client import LLMClient
from llm.schemas import MetricLabelResolution


class _UpsertingStore(Protocol):
    def upsert(self, proposition: Proposition) -> None: ...

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You label one sentence from a financial filing with the metric(s) it reports.

You are given the sentence and a fixed list of allowed metric names. Return
only names from that list that the sentence genuinely states a value for.

ABSOLUTE RULE
You never invent a metric name that is not in the given list. If the sentence
matches none of them, return an empty list. Guessing a close-but-wrong label
is a failure; an empty list is a correct answer.
"""

# Canonical metric -> phrases that mean it, all lowercase. Keep phrases
# specific: a vague phrase shared by two metrics mislabels both.
#
# Step 2.2 (2026-10-17): the extra phrase per metric below is standard
# financial English (GAAP/IFRS and common British/Nigerian usage), not
# copied from any one glossary -- a deliberately conservative first pass,
# owner-reviewed before being seeded. Genuinely ambiguous synonyms were
# left out on purpose rather than guessed at: "stock" for inventories
# (collides with equity stock), "receivables"/"payables"/"debtors"/
# "creditors" alone (too generic, same collision risk the module's own
# comment above already warns about), "EBIT" for operating_income (not
# always the same figure -- a real accounting difference, not just a
# naming one), "gross income"/"profit margin" alone (each already
# ambiguous between two of this dictionary's own metrics), and three
# first drafts caught and dropped by this file's own tests for the exact
# same reason as the documented gaap/non-gaap limit below: "operating
# profit margin" and "gross profit margin" each literally contain another
# metric's own phrase ("operating profit", "gross profit"), and "sales
# revenue" literally contains "revenue" -- each would have silently
# double-labelled, not just the one metric intended.
DEFAULT_METRIC_ALIASES: Mapping[str, tuple[str, ...]] = {
    "operating_margin": ("operating margin",),
    "net_profit_margin": ("net profit margin", "net margin", "pat margin"),
    "gross_margin": ("gross margin",),
    "net_revenue": ("net revenue",),
    "total_revenue": (
        "total revenue", "total net sales", "net sales", "turnover", "topline", "total sales",
        "operating revenue", "operating revenues", "total revenues",
    ),
    "revenue": ("revenue",),
    "basic_eps": ("basic earnings per share", "basic eps"),
    "diluted_eps": ("diluted earnings per share", "diluted eps"),
    "gaap": ("gaap",),
    "non_gaap": ("non-gaap", "non gaap"),
    "accounts_payable": ("accounts payable", "trade payables", "trade creditors"),
    # PepsiCo/AES write this as "accounts and notes receivable" or "net
    # receivables", not "accounts receivable" -- added so the real figure gets found.
    "accounts_receivable": (
        "accounts receivable", "trade receivables", "trade debtors",
        "accounts and notes receivable", "net receivables",
    ),
    "capital_expenditure": ("capital expenditure", "capital expenditures", "capex", "capital spending"),
    "gross_profit": ("gross profit",),
    "inventories": ("inventories", "inventory", "stock-in-trade"),
    "net_income": ("net income", "profit after tax", "bottom line"),
    "operating_income": ("operating income", "operating profit"),
    "total_current_assets": ("total current assets",),
    "total_current_liabilities": ("total current liabilities",),
}


# These five are only ever true "as of" one date. Text about them phrased
# "for/during the year ended" is almost always the cash-flow line of the same name, a different fact.
_BALANCE_SHEET_METRICS = frozenset({
    "accounts_payable", "accounts_receivable", "inventories",
    "total_current_assets", "total_current_liabilities",
})
_PERIOD_PHRASE_RE = re.compile(r"\b(?:for|during)\s+the\b[^.]{0,40}\bended\b", re.IGNORECASE)
_AS_OF_RE = re.compile(r"\bas\s+of\b", re.IGNORECASE)


def _is_period_flow_not_balance(metric: str, lowered_text: str) -> bool:
    if metric not in _BALANCE_SHEET_METRICS:
        return False
    return bool(_PERIOD_PHRASE_RE.search(lowered_text)) and not _AS_OF_RE.search(lowered_text)


# Public names for the reranker's ask-match check, so the "balance vs flow" rule lives in one place.
BALANCE_SHEET_METRICS = _BALANCE_SHEET_METRICS
is_period_flow_not_balance = _is_period_flow_not_balance


def label_metrics(text: str, aliases: Mapping[str, Sequence[str]] = DEFAULT_METRIC_ALIASES) -> tuple[str, ...]:
    """Every metric whose phrase appears as a whole word/phrase in `text`,
    sorted for a stable, testable order. Empty means "not recognized yet",
    never a guess.

    A phrase match right before "per share" is skipped: "net income per
    share" is EPS wording, not a net income value, even though the words
    "net income" sit right there. Filings also write this as "shares used
    to compute ... net income per share" for the share count, never the
    value itself, so this case stays unlabelled rather than guessed."""
    lowered = text.lower()
    matched = {
        metric
        for metric, phrases in aliases.items()
        if any(
            re.search(rf"\b{re.escape(phrase)}\b", lowered)
            and not re.search(rf"\b{re.escape(phrase)}\b\s*per\s+share\b", lowered)
            for phrase in phrases
        )
        and not _is_period_flow_not_balance(metric, lowered)
    }
    return tuple(sorted(matched))


def label_metrics_llm_fallback(
    text: str, llm_client: LLMClient, lexicon: Sequence[str] = tuple(DEFAULT_METRIC_ALIASES)
) -> tuple[str, ...]:
    """Tier 3 -- only for a fact the rules found nothing at all for. The
    model may only pick from `lexicon`; anything else it returns is
    dropped, never trusted verbatim. Schema-invalid output is dropped too
    (unlabelled, not guessed), same as numeric_extractor's tier 3.

    The balance-vs-period check applies here too, or the model would just
    relabel what tier 1 deliberately left unlabelled."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "sentence": text,
        "allowed_metrics": list(lexicon),
    }
    raw_response = llm_client.complete(payload)

    try:
        resolution = MetricLabelResolution.model_validate(raw_response)
    except Exception:
        return ()

    allowed = set(lexicon)
    lowered = text.lower()
    return tuple(
        sorted(
            metric for metric in resolution.metrics
            if metric in allowed and not _is_period_flow_not_balance(metric, lowered)
        )
    )


def label_metrics_with_fallback(
    text: str,
    aliases: Mapping[str, Sequence[str]] = DEFAULT_METRIC_ALIASES,
    llm_client: Optional[LLMClient] = None,
) -> tuple[str, ...]:
    """Rules first, always, at zero cost. The model is only asked about a
    fact the rules matched nothing on, and only if a client is given --
    passing none keeps a fact unlabelled exactly like before this fallback
    existed."""
    rule_based = label_metrics(text, aliases=aliases)
    if rule_based or llm_client is None:
        return rule_based
    return label_metrics_llm_fallback(text, llm_client, lexicon=tuple(aliases))


def label_proposition_if_needed(
    proposition: Proposition,
    metadata_store: _UpsertingStore,
    aliases: Mapping[str, Sequence[str]] = DEFAULT_METRIC_ALIASES,
    llm_client: Optional[LLMClient] = None,
) -> Proposition:
    """One proposition, labelled only if it has no metrics yet: rules
    first, free; the model only for what rules miss, and only if a client
    is given. Writes back to the store only when the label actually
    changes, so calling this twice on an already-labelled fact is a no-op
    the second time. Built for the online on-demand hook (decision 26,
    pipeline_c4.py), which labels a handful of reranked candidates and
    needs the fresh object back immediately for the same request to use.
    `offline/labeling/backfill_metrics.py` keeps its own loop rather than
    calling this -- it labels every fact in bulk and reports separate
    rules-vs-model counts, which this one-proposition-at-a-time helper
    doesn't track."""
    if proposition.metrics:
        return proposition
    new_metrics = label_metrics_with_fallback(proposition.text, aliases=aliases, llm_client=llm_client)
    if new_metrics and new_metrics != proposition.metrics:
        fresh = proposition.model_copy(update={"metrics": new_metrics})
        metadata_store.upsert(fresh)
        return fresh
    return proposition
