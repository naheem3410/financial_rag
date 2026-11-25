"""Fixed-size chunker — C0 naive RAG baseline only. Character-window
slicing, no model, the textbook naive-RAG strategy this cell represents.

Never used by C1-C4 (those index at proposition granularity via an LLM
call) — this builds C0's separate reference index so its numbers reflect a
plain chunk-and-embed pipeline, not a degraded version of the real system.
Chunks stay within one page (not spanning boundaries) so each still carries
a real page number for citation, without changing what "naive" means here.
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
    """(offset, chunk_text) pairs, sliding a fixed window with overlap
    characters shared between chunks. A short page yields one whole chunk."""
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
    """One Proposition per (page, window) — reuses the schema as a storage
    convenience for build_index, not a claim the chunk is one atomic fact
    like C1-C4's real propositions. numeric is always None here."""
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
