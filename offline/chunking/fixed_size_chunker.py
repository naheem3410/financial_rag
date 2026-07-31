"""Fixed-size chunker — C0 naive RAG baseline only. Pure character-window
slicing, no model involved — the textbook naive-RAG chunking strategy this
cell exists to represent.

Never used by C1-C4. Those cells index at proposition granularity
(offline/propositions/segmenter_llm.py, an LLM call) — this module exists
solely to build C0's separate reference index, so C0's numbers show what a
standard, un-augmented chunk-and-embed pipeline achieves on this corpus, not
a degraded version of the real system.

Chunks are built per page, not spanning page boundaries, so each chunk
still carries a real page number for citation/scoring — a true naive
chunker usually flattens the whole document into one text stream first, but
per-page chunking preserves page-level ground truth without materially
changing what "naive, un-atomized, fixed windows" means.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

from contracts.models import Confidence, Proposition, PropositionMetadata
from offline.parsers.layout_parser import ParsedPage

DEFAULT_CHUNK_SIZE = 2000  # characters, roughly 400-500 tokens
DEFAULT_OVERLAP = 200


def _chunk_id(source_uri: str, page: int, offset: int) -> str:
    key = f"{source_uri}|{page}|{offset}"
    return "c_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def chunk_page_fixed_size(
    page: ParsedPage, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP
) -> list[tuple[int, str]]:
    """(offset, chunk_text) pairs for one page's text, sliding a fixed-size
    window with `overlap` characters shared between consecutive chunks. A
    page shorter than `chunk_size` yields exactly one chunk (its full text,
    no padding)."""
    text = page.text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [(0, text)]

    chunks: list[tuple[int, str]] = []
    step = chunk_size - overlap
    offset = 0
    while offset < len(text):
        chunk_text = text[offset : offset + chunk_size]
        if chunk_text.strip():
            chunks.append((offset, chunk_text))
        if offset + chunk_size >= len(text):
            break
        offset += step
    return chunks


def chunk_document_fixed_size(
    pages: Sequence[ParsedPage],
    source_uri: str,
    company: str,
    filing_type: str,
    filing_date: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Proposition]:
    """One fixed-size-chunk `Proposition` per (page, window) — reuses the
    `Proposition` schema purely as a storage/indexing convenience (drop-in
    compatible with `offline.indexing.build.build_index`), not a claim that
    a raw chunk is an atomic single-fact proposition the way C1-C4's real
    propositions are. `numeric=None` always — no per-chunk numeric/period
    extraction happens for the naive baseline; that structured tagging is
    itself part of what C1-C4 add over C0."""
    chunks: list[Proposition] = []
    for page in pages:
        for offset, chunk_text in chunk_page_fixed_size(page, chunk_size, overlap):
            chunks.append(
                Proposition(
                    proposition_id=_chunk_id(source_uri, page.page_number, offset),
                    text=chunk_text,
                    provenance_tier="retrieved",
                    ingestion_path="A_unstructured",
                    numeric=None,
                    confidence=Confidence(extraction_tier="rules"),
                    metadata=PropositionMetadata(
                        company=company, filing_type=filing_type, filing_date=filing_date,
                        section="", page=page.page_number, source_uri=source_uri,
                    ),
                )
            )
    return chunks
