"""Extend C0's naive-RAG index from 5 filings to all 15 (KNOWN_ISSUES.md
Phase 2 to-do list, "naive RAG" comparison setup).

C0 is deliberately NOT built with `run_full_build` -- that function exists
to checkpoint expensive, fallible LLM segmentation calls, and C0 has none
(README.md section 8, CLAUDE.md: C0 is fixed-size chunking, zero LLM
calls, fully deterministic). Chunking the same PDF twice always produces
the same chunks, so there is nothing a checkpoint would protect against
here -- a plain full rebuild from scratch over all 15 filings each run is
the simplest correct approach, and is cheap enough (local parsing +
chunking + embedding only, no network calls that can fail or cost money)
that re-running it from zero costs a few minutes, not a few hours.

This intentionally mirrors `scripts/index_missing_filings.py`'s doc list
(the same 15 filings, read from `data/full_build_checkpoint_v2.json`'s
`completed` keys so the two indexes never drift apart), but NOT its
checkpointing machinery or its target database/index path -- those stay
`financial_rag_v2` / `dense_index_v2` for C1-C4, this script only ever
writes `financial_rag_c0` / `dense_index_c0`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline.chunking.fixed_size_chunker import chunk_document_fixed_size
from offline.corpus import resolve_pdf_paths
from offline.embedding.bge_embedder import BGEEmbedder
from offline.indexing.build import build_index
from offline.indexing.metadata_store_postgres import PostgresMetadataStore
from offline.parsers.layout_parser import parse_document

REPO_BACKEND = Path(__file__).resolve().parents[1]

# Same 15 doc_names / company strings as scripts/index_missing_filings.py
# (ALREADY_INDEXED + NEW_DOCS there) -- kept as one literal dict here too
# rather than importing that script's module-level constants, since this
# script is meant to stand alone and the two indexes are independent builds.
DOC_NAME_TO_COMPANY = {
    "3M_2018_10K": "3M Company",
    "3M_2022_10K": "3M Company",
    "3M_2023Q2_10Q": "3M Company",
    "ACTIVISIONBLIZZARD_2019_10K": "Activision Blizzard, Inc.",
    "ADOBE_2016_10K": "Adobe Systems Incorporated",
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

# filing_type/filing_date are read straight off the doc_name the same way
# offline/corpus.py:parse_doc_name does it for the real build, kept as a
# small local helper so this script has no other dependency on that module's
# regex details beyond this one call.
from offline.corpus import parse_doc_name  # noqa: E402

CHECKPOINT_PATH = REPO_BACKEND / "data" / "full_build_checkpoint_v2.json"
PDF_DIR = REPO_BACKEND / "data" / "pdfs"
DENSE_INDEX_PATH = REPO_BACKEND / "data" / "index" / "dense_index_c0"
POSTGRES_DSN = "postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_c0"


def _doc_names_from_checkpoint(checkpoint_path: Path) -> list[str]:
    data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    return sorted(data["completed"].keys())


def main() -> None:
    doc_names = _doc_names_from_checkpoint(CHECKPOINT_PATH)
    if set(doc_names) != set(DOC_NAME_TO_COMPANY):
        raise ValueError(
            "doc_names from full_build_checkpoint_v2.json no longer match this "
            f"script's DOC_NAME_TO_COMPANY -- update it. checkpoint={sorted(doc_names)} "
            f"script={sorted(DOC_NAME_TO_COMPANY)}"
        )

    pdf_paths = resolve_pdf_paths(set(doc_names), PDF_DIR)
    embedder = BGEEmbedder()

    all_chunks = []
    for doc_name in doc_names:
        company = DOC_NAME_TO_COMPANY[doc_name]
        fields = parse_doc_name(doc_name)
        filing_type = fields.get("filing_type", "10-K")
        filing_date = str(fields.get("fiscal_year", ""))

        parsed = parse_document(pdf_paths[doc_name])
        chunks = chunk_document_fixed_size(
            parsed.pages,
            source_uri=str(pdf_paths[doc_name]),
            company=company,
            filing_type=filing_type,
            filing_date=filing_date,
        )
        all_chunks.extend(chunks)
        print(f"[build_c0_index] {doc_name}: {len(chunks)} chunks", flush=True)

    # A true from-scratch rebuild (this script's whole premise, see module
    # docstring) must also clear out the 5-doc build's old rows -- without
    # this, any old proposition_id that happens to hash differently than its
    # replacement (e.g. a different absolute source_uri path) is orphaned in
    # Postgres but absent from the freshly-written FAISS/sparse files,
    # breaking the three-index invariant (CLAUDE.md section 3).
    store = PostgresMetadataStore(POSTGRES_DSN)
    store.clear()
    store.close()

    manifest = build_index(
        propositions=all_chunks,
        embedder=embedder,
        dense_index_path=DENSE_INDEX_PATH,
        postgres_dsn=POSTGRES_DSN,
        indexed_doc_names=doc_names,
    )

    print("\n=== DONE ===")
    print(f"database: {POSTGRES_DSN}")
    print(f"dense index path: {DENSE_INDEX_PATH}")
    print(f"chunk/proposition count: {manifest.proposition_count}")
    print(f"indexed_doc_names: {manifest.indexed_doc_names}")


if __name__ == "__main__":
    main()
