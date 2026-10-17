"""The concrete `ingest_fn` for a non-finance document: turns its bytes
into PolicyFact rows, using the rule-based chunker and extractor built for
this. Plugs into offline/documents/upload_job.py's pluggable content step.
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Optional

from llm.client import LLMClient
from offline.documents.document_card import DocumentCard
from offline.documents.policy_chunker import chunk_document_into_rule_blocks
from offline.documents.policy_fact_store import PolicyFactStore
from offline.documents.rule_extractor import rule_block_to_fact
from offline.documents.upload_job import IngestResult
from offline.parsers.layout_parser import parse_document


def ingest_policy_document(
    card: DocumentCard, file_bytes: bytes, store: PolicyFactStore, antecedent_llm_client: Optional[LLMClient] = None,
) -> IngestResult:
    """`parse_document` needs a real path, so the bytes are written to a
    short-lived temp file (same suffix as the upload) and removed again --
    nothing about the upload's own saved copy is touched here.

    `antecedent_llm_client` left out (the default) costs nothing and skips
    antecedent extraction entirely, same as before that fallback existed."""
    suffix = Path(card.file_name).suffix or ".txt"
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / f"upload{suffix}"
        tmp_path.write_bytes(file_bytes)
        parsed = parse_document(tmp_path)

    blocks = chunk_document_into_rule_blocks(parsed)
    fact_ids: list[str] = []
    for block in blocks:
        fact_id = f"{card.id}_{uuid.uuid4().hex[:8]}"
        fact = rule_block_to_fact(
            block, fact_id=fact_id, document_id=card.id, source_uri=card.file_name, llm_client=antecedent_llm_client,
        )
        store.upsert(fact)
        fact_ids.append(fact_id)
    return IngestResult(fact_ids=tuple(fact_ids))


def remove_policy_facts(store: PolicyFactStore, fact_ids) -> None:
    store.delete_many(fact_ids)


def check_policy_facts_stored(store: PolicyFactStore, fact_ids) -> bool:
    """The three-store check for policy facts: today there is only one real
    store (Postgres), so this checks every fact id the ingest step reported
    actually landed there -- the same role the dense/keyword index
    comparison plays for financial facts, once there is more than one
    store to compare."""
    stored_ids = store.ids()
    return all(fid in stored_ids for fid in fact_ids)
