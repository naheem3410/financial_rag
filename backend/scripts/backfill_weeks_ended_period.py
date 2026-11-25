"""One-off backfill for the 2026-11-17 numeric_extractor fix.

Re-runs extract_tier2 (pure regex, zero LLM calls, zero dollars) over every
proposition already sitting in financial_rag_v2 and financial_rag_c0 whose
text matches "<N> weeks ended <date>" -- the phrasing the old
_PERIOD_ENDED_RE missed. Updates only the rows whose numeric/period actually
changes, in both Postgres (fiscal_year/quarter columns + the numeric field
inside the data jsonb) and the matching full_build_checkpoint_v2 sidecar
file on disk, so a future resume from checkpoint sees the fix too.

Does not touch the dense or sparse index: both are built from proposition
text only, which was already correct -- nothing to re-embed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.models import Confidence, Proposition
from offline.extraction.numeric_extractor import extract_tier2
from offline.indexing.metadata_store_postgres import PostgresMetadataStore

_WEEKS_ENDED_RE = re.compile(r"\b\d+\s+weeks?\s+ended\b", re.IGNORECASE)

DSNS = {
    "financial_rag_v2": "postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_v2",
    "financial_rag_c0": "postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_c0",
}


def backfill_db(db_name: str, dsn: str) -> list[tuple[str, str]]:
    store = PostgresMetadataStore(dsn)
    changed: list[tuple[str, str]] = []
    try:
        candidates = store.query()  # all() would also work; query() with no args is the same
        for prop in candidates:
            if not _WEEKS_ENDED_RE.search(prop.text):
                continue
            if prop.numeric is not None:
                continue  # already resolved (e.g. by tier3 previously) -- leave alone
            record = extract_tier2(prop.text)
            if record is None:
                continue  # still unresolved after the fix -- not this bug
            updated = prop.model_copy(
                update={
                    "numeric": record,
                    "confidence": Confidence(
                        ocr_confidence=prop.confidence.ocr_confidence,
                        extraction_tier="rules",
                    ),
                }
            )
            store.upsert(updated)
            changed.append((prop.proposition_id, prop.text))
    finally:
        store.close()
    return changed


def backfill_checkpoint_sidecar(doc_name: str) -> int:
    path = Path(__file__).resolve().parents[1] / "data" / f"full_build_checkpoint_v2.{doc_name}.propositions.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    n_changed = 0
    for row in data:
        text = row["text"]
        if not _WEEKS_ENDED_RE.search(text):
            continue
        if row.get("numeric") is not None:
            continue
        record = extract_tier2(text)
        if record is None:
            continue
        row["numeric"] = json.loads(record.model_dump_json())
        row["confidence"]["extraction_tier"] = "rules"
        n_changed += 1
    if n_changed:
        path.write_text(json.dumps(data), encoding="utf-8")
    return n_changed


if __name__ == "__main__":
    for db_name, dsn in DSNS.items():
        changed = backfill_db(db_name, dsn)
        print(f"{db_name}: {len(changed)} propositions backfilled")
        for pid, text in changed[:5]:
            print(f"  {pid}: {text[:90]}")

    for doc_name in ("COSTCO_2021_10K", "PEPSICO_2021_10K"):
        n = backfill_checkpoint_sidecar(doc_name)
        print(f"checkpoint sidecar {doc_name}: {n} propositions backfilled")
