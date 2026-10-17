"""Labels every fact already in the database with its metrics, using the
rule-based labeler. Safe to run twice: a fact's label only ever comes from
its own text, so re-running it changes nothing once every fact is settled.
Run as its own script, separate from any other database change (step 0.3's
"the metric backfill is its own separate script" decision).
"""
from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from contracts.models import Proposition
from llm.client import LLMClient
from offline.indexing.metadata_store_postgres import PostgresMetadataStore
from offline.labeling.metric_labeler import DEFAULT_METRIC_ALIASES, label_metrics, label_metrics_llm_fallback


@dataclass(frozen=True)
class BackfillReport:
    total: int
    labelled: int
    labelled_by_rules: int
    labelled_by_model: int
    changed: int

    @property
    def labelled_share(self) -> float:
        return self.labelled / self.total if self.total else 0.0


def backfill_metrics(
    metadata_store: PostgresMetadataStore,
    aliases: Mapping[str, Sequence[str]] = DEFAULT_METRIC_ALIASES,
    llm_client: Optional[LLMClient] = None,
) -> BackfillReport:
    """Relabels every fact from its own text: rules first, at zero cost;
    the model (if a client is given) only for a fact the rules found
    nothing on. Only writes back the ones whose label actually changed, so
    a repeat run is a no-op. `llm_client=None` (the default) costs nothing
    and behaves exactly as before this fallback existed -- pass one only
    when you mean to spend on the leftovers."""
    propositions = metadata_store.all()
    labelled_by_rules = 0
    labelled_by_model = 0
    changed = 0
    for proposition in propositions:
        rule_metrics = label_metrics(proposition.text, aliases=aliases)
        if rule_metrics:
            labelled_by_rules += 1
            new_metrics = rule_metrics
        elif llm_client is not None:
            new_metrics = label_metrics_llm_fallback(proposition.text, llm_client, lexicon=tuple(aliases))
            if new_metrics:
                labelled_by_model += 1
        else:
            new_metrics = ()
        if new_metrics != proposition.metrics:
            metadata_store.upsert(proposition.model_copy(update={"metrics": new_metrics}))
            changed += 1
    return BackfillReport(
        total=len(propositions), labelled=labelled_by_rules + labelled_by_model,
        labelled_by_rules=labelled_by_rules, labelled_by_model=labelled_by_model, changed=changed,
    )


def sample_for_review(metadata_store: PostgresMetadataStore, n: int = 100, seed: Optional[int] = None) -> list[Proposition]:
    """A random sample for the human spot-check the plan calls for -- this
    function only picks the sample, a person still has to look at it and
    judge whether each label is right."""
    propositions = metadata_store.all()
    rng = random.Random(seed)
    return rng.sample(propositions, k=min(n, len(propositions)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument(
        "--config", default=None,
        help="Experiment config with a metric_label_fallback call site configured. "
        "Omit to run rules only, at zero cost (the default).",
    )
    args = parser.parse_args()

    llm_client = None
    if args.config:
        from config.schema import load_config
        from llm.routing import build_llm_client

        llm_client = build_llm_client("metric_label_fallback", load_config(args.config))

    store = PostgresMetadataStore(args.dsn)
    try:
        report = backfill_metrics(store, llm_client=llm_client)
        print(f"total facts: {report.total}")
        print(f"labelled: {report.labelled} ({report.labelled_share:.1%})")
        print(f"  by rules: {report.labelled_by_rules}")
        print(f"  by model: {report.labelled_by_model}")
        print(f"changed this run: {report.changed}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
