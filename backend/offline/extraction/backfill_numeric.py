"""Fills in `numeric` for facts already in the database whose extraction
never ran, or ran before `extract_tier2`'s "at <date>" pattern existed.
Same shape as `offline/labeling/backfill_metrics.py`: rules only, zero
LLM cost, safe to run twice -- a fact only ever gets a number from its
own text, so once every fact is settled, re-running changes nothing.

Never touches a proposition that already has a `numeric` value: this is
purely about facts nobody has extracted yet, never re-deciding one
already on record.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from offline.extraction.numeric_extractor import extract_tier2
from offline.indexing.metadata_store_postgres import PostgresMetadataStore


@dataclass(frozen=True)
class NumericBackfillReport:
    total: int
    already_had_numeric: int
    recovered: int

    @property
    def still_unresolved(self) -> int:
        return self.total - self.already_had_numeric - self.recovered


def backfill_numeric(metadata_store: PostgresMetadataStore) -> NumericBackfillReport:
    propositions = metadata_store.all()
    already_had_numeric = 0
    recovered = 0
    for proposition in propositions:
        if proposition.numeric is not None:
            already_had_numeric += 1
            continue
        numeric = extract_tier2(proposition.text)
        if numeric is None:
            continue
        recovered += 1
        metadata_store.upsert(proposition.model_copy(update={"numeric": numeric}))
    return NumericBackfillReport(
        total=len(propositions), already_had_numeric=already_had_numeric, recovered=recovered,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()

    store = PostgresMetadataStore(args.dsn)
    try:
        report = backfill_numeric(store)
        print(f"total facts: {report.total}")
        print(f"already had numeric: {report.already_had_numeric}")
        print(f"recovered this run: {report.recovered}")
        print(f"still unresolved (genuinely non-numeric, or tier 2 can't parse the period): {report.still_unresolved}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
