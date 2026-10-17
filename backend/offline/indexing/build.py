"""Build once, reuse unchanged across C0-C4 — segmentation is an LLM call,
so rebuilding per cell would give each cell a different retrieval core.
load_index reopens the persisted build without touching the LLM again;
BM25 postings get rebuilt cheaply since tokenization is deterministic.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

from contracts.interfaces import Embedder
from contracts.models import Proposition
from offline.indexing.dense_index_faiss import PersistentDenseIndex
from offline.indexing.index_writers import SparseIndex, SparseIndexFormatError
from offline.indexing.metadata_store_postgres import PostgresMetadataStore


@dataclass(frozen=True)
class BuildManifest:
    build_id: str
    proposition_count: int
    embedder_model_name: str
    embedder_revision: str
    embedder_dimension: int
    # doc set this build indexed (offline/corpus.py's resolved doc_names) —
    # scope must match across cells, so a drift shows up here like build_id would.
    indexed_doc_names: tuple[str, ...] = ()
    # pages skipped at index time, by reason — kept on the manifest, not just
    # logged, so a "temporary" skip stays auditable after the run ends.
    skipped_pages_by_reason: dict[str, int] = field(default_factory=dict)
    # result of gold_overlap.assert_no_gold_page_overlap; None if not run.
    # a run you can't reconstruct from its manifest isn't reportable.
    gold_overlap_clean: Optional[bool] = None


@dataclass(frozen=True)
class IndexBundle:
    dense_index: PersistentDenseIndex
    sparse_index: SparseIndex
    metadata_store: PostgresMetadataStore


def _sparse_index_path(dense_index_path: Union[str, Path]) -> Path:
    return Path(str(dense_index_path) + ".sparse.json")


def compute_build_id(propositions: Sequence[Proposition], embedder: Embedder) -> str:
    """Same propositions + same embedder identity always hash to the same
    build_id, so two builds over the same input are provably identical."""
    ids_joined = "|".join(sorted(p.proposition_id for p in propositions))
    key = f"{ids_joined}::{embedder.model_name}::{embedder.revision}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def build_index(
    propositions: Sequence[Proposition],
    embedder: Embedder,
    dense_index_path: Union[str, Path],
    postgres_dsn: str,
    indexed_doc_names: Sequence[str] = (),
    skipped_pages_by_reason: Optional[dict[str, int]] = None,
    gold_overlap_clean: Optional[bool] = None,
) -> BuildManifest:
    """Runs once, persists to FAISS + Postgres. Call load_index for every
    cell afterward instead (see module docstring). The doc-names/skip/
    overlap args are passed through from the caller, not computed here."""
    metadata_store = PostgresMetadataStore(postgres_dsn)
    dense_index = PersistentDenseIndex(dimension=embedder.dimension)
    sparse_index = SparseIndex()

    if propositions:
        vectors = embedder.encode_passages([p.text for p in propositions])
        for proposition, vector in zip(propositions, vectors):
            dense_index.upsert(proposition.proposition_id, vector)
            sparse_index.upsert(proposition.proposition_id, proposition.text)
            metadata_store.upsert(proposition)

    dense_index.save(dense_index_path)
    sparse_index.save(_sparse_index_path(dense_index_path))
    metadata_store.close()

    return BuildManifest(
        build_id=compute_build_id(propositions, embedder),
        proposition_count=len(propositions),
        embedder_model_name=embedder.model_name,
        embedder_revision=embedder.revision,
        embedder_dimension=embedder.dimension,
        indexed_doc_names=tuple(sorted(indexed_doc_names)),
        skipped_pages_by_reason=dict(skipped_pages_by_reason or {}),
        gold_overlap_clean=gold_overlap_clean,
    )


def load_index(dense_index_path: Union[str, Path], postgres_dsn: str, dimension: int) -> IndexBundle:
    """Reopens persisted state — no LLM call, nothing gets re-embedded.

    The keyword index is loaded from its own saved file (step 1.3), not
    rebuilt from Postgres text on every startup. A build made before this
    step existed has no such file yet -- that one time, it is rebuilt from
    Postgres and saved, so every load after that is fast."""
    metadata_store = PostgresMetadataStore(postgres_dsn)
    dense_index = PersistentDenseIndex.load(dense_index_path, dimension=dimension)

    sparse_path = _sparse_index_path(dense_index_path)
    try:
        sparse_index = SparseIndex.load(sparse_path)
    except (FileNotFoundError, SparseIndexFormatError):
        sparse_index = SparseIndex()
        for proposition in metadata_store.all():
            sparse_index.upsert(proposition.proposition_id, proposition.text)
        sparse_index.save(sparse_path)

    return IndexBundle(dense_index=dense_index, sparse_index=sparse_index, metadata_store=metadata_store)
