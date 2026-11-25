"""Index the 10 FinanceBench filings the 366-item benchmark still needs
(KNOWN_ISSUES.md Phase 2 to-do list, item 1). Reuses the same checkpointed
`run_full_build` the original 5-document build used, pointed at the SAME
checkpoint file and the SAME dense index path -- this is deliberate, not
an oversight: `build_index` writes a brand-new FAISS file every call, so
calling it with only the 10 new docs would overwrite and lose the 5
already-indexed ones. Passing all 15 doc_names together means the 5
already-completed ones are loaded straight from their checkpoint sidecar
(instant, zero cost, zero LLM calls) and only the 10 new ones actually
hit the model, then one single build_index call writes a complete,
correct 15-document index.

Safe to interrupt and rerun exactly as-is at any time -- a crash,
closed laptop lid, or network drop loses at most the one batch that was
in flight, never a completed document or a completed batch
(offline/full_build.py's own per-document and per-batch checkpoint
sidecars). Rerunning this script re-bills nothing already paid for.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.schema import load_config
from offline.corpus import resolve_pdf_paths
from offline.embedding.bge_embedder import BGEEmbedder
from offline.full_build import run_full_build

REPO_BACKEND = Path(__file__).resolve().parents[1]

ALREADY_INDEXED = [
    "3M_2018_10K",
    "3M_2022_10K",
    "3M_2023Q2_10Q",
    "ACTIVISIONBLIZZARD_2019_10K",
    "ADOBE_2016_10K",
]

# Exact registrant name as printed on each filing's own cover page
# ("Exact name of registrant as specified in its charter") -- never
# guessed from memory, read straight from the PDF text. ADOBE_2022_10K is
# a real, pre-existing alias gap worth flagging: Adobe's legal name
# changed from "Adobe Systems Incorporated" (on file for ADOBE_2016_10K,
# already indexed) to "Adobe Inc." in 2018 -- the entities-table aliasing
# that would link these two names together is decision 9/10 in
# KNOWN_ISSUES.md, still not built. Until it is, a question using the old
# name won't find this filing by metadata filter alone (the "try again
# with other names" retry road only knows aliases already in the
# registry, and these two legal names were never linked there).
NEW_DOCS = {
    "ADOBE_2022_10K": "Adobe Inc.",
    "AES_2022_10K": "The AES Corporation",
    "AMERICANWATERWORKS_2020_10K": "American Water Works Company, Inc.",
    "BESTBUY_2019_10K": "Best Buy Co., Inc.",
    "BOEING_2022_10K": "The Boeing Company",
    "COSTCO_2021_10K": "Costco Wholesale Corporation",
    "GENERALMILLS_2020_10K": "General Mills, Inc.",
    "GENERALMILLS_2022_10K": "General Mills, Inc.",
    "LOCKHEEDMARTIN_2020_10K": "Lockheed Martin Corporation",
    "PEPSICO_2021_10K": "PepsiCo, Inc.",
}

CHECKPOINT_PATH = REPO_BACKEND / "data" / "full_build_checkpoint_v2.json"
DENSE_INDEX_PATH = REPO_BACKEND / "data" / "index" / "dense_index_v2"
POSTGRES_DSN = "postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_v2"
PDF_DIR = REPO_BACKEND / "data" / "pdfs"

# "a few dollars" per KNOWN_ISSUES.md's own estimate for 10 filings; the
# first 5 cost $2.93 total. Generous headroom so a legitimately larger
# filing (Boeing, Lockheed) doesn't trip the cap mid-run -- checkpointing
# means raising this and rerunning loses nothing either way.
MAX_SPEND_USD = 25.0
MAX_WORKERS = 8


def main() -> None:
    doc_names = ALREADY_INDEXED + list(NEW_DOCS.keys())
    pdf_paths = resolve_pdf_paths(set(doc_names), PDF_DIR)
    config = load_config(str(REPO_BACKEND / "config" / "experiments" / "c1_baseline.yaml"))
    embedder = BGEEmbedder()

    def progress(message: str) -> None:
        print(f"[index_missing_filings] {message}", flush=True)

    manifest = run_full_build(
        doc_names=doc_names,
        config=config,
        pdf_paths=pdf_paths,
        company_names=NEW_DOCS,
        checkpoint_path=CHECKPOINT_PATH,
        max_spend_usd=MAX_SPEND_USD,
        embedder=embedder,
        dense_index_path=DENSE_INDEX_PATH,
        postgres_dsn=POSTGRES_DSN,
        config_hash=config.experiment_id,
        max_workers=MAX_WORKERS,
        progress_callback=progress,
    )

    print("\n=== DONE ===")
    print(f"propositions indexed (all 15 docs): {manifest.index_build.proposition_count}")
    print(f"indexed_doc_names: {manifest.index_build.indexed_doc_names}")
    print(f"total cost this run: ${manifest.total_cost_usd:.4f}")
    print(f"total requests: {manifest.total_requests}")


if __name__ == "__main__":
    main()
