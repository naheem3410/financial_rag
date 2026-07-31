"""Index build orchestration — build once, reuse unchanged across C0-C4.

Proposition segmentation is an LLM call. If the offline index were rebuilt
between experiment cells, each cell's dense/metadata stores could end up
holding a different set of propositions — a different retrieval core per
cell, which confounds every measured NPCE/CoVe delta with retrieval noise
rather than a real effect. All cells need to share an identical retrieval
core.

`build_index` runs the offline pipeline's persistence step exactly once and
returns a `BuildManifest` recording a `build_id` — record this in the run
manifest for every experiment cell, so results are traceable to the exact
index build that produced them. `load_index` reopens that persisted state
without calling the LLM again: the in-memory `SparseIndex` (BM25) is
cheaply rebuilt from the persisted propositions on every load, since
tokenization is deterministic and needs no model call — only the
propositions themselves need protecting from re-derivation, not the
postings built from them.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

from contracts.interfaces import Embedder
from contracts.models import Proposition
from offline.indexing.dense_index_faiss import PersistentDenseIndex
from offline.indexing.index_writers import SparseIndex
from offline.indexing.metadata_store_postgres import PostgresMetadataStore


@dataclass(frozen=True)
class BuildManifest:
    build_id: str
    proposition_count: int
    embedder_model_name: str
    embedder_revision: str
    embedder_dimension: int
    # The exact document set this build indexed (e.g. offline/corpus.py's
    # resolved doc_names). Corpus scope must be identical across C0-C4 —
    # recording it here makes a scope drift between cells detectable from
    # the manifest alone, the same way build_id makes a proposition-set
    # drift detectable.
    indexed_doc_names: tuple[str, ...] = ()
    # Pages skipped at index time, aggregated by reason (e.g.
    # {"risk_factors_item_1a": 30, "cover": 4, ...}) — recorded here, not
    # just logged transiently, so a skip that's meant to be temporary and
    # run-scoped is auditable from the manifest a build produced, not just
    # from the run's console output.
    skipped_pages_by_reason: dict[str, int] = field(default_factory=dict)
    # Result of offline.gold_overlap.assert_no_gold_page_overlap for this
    # build's skipped pages against the question set's gold evidence pages —
    # `None` if the check was not run (e.g. a build with no associated
    # question set), `True`/`False` otherwise. "A run I can't fully
    # reconstruct from its manifest isn't reportable" — the safety
    # assertion's *result*, not just the code that can perform it, belongs
    # on the record.
    gold_overlap_clean: Optional[bool] = None


@dataclass(frozen=True)
class IndexBundle:
    dense_index: PersistentDenseIndex
    sparse_index: SparseIndex
    metadata_store: PostgresMetadataStore


def compute_build_id(propositions: Sequence[Proposition], embedder: Embedder) -> str:
    """Deterministic: identical propositions + identical embedder identity
    always produce the same `build_id`, so two `build_index` runs over the
    same input are provably the same build, not just probably the same."""
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
    """Runs once — persists to FAISS (dense) and Postgres/JSONB (metadata).
    Call `load_index` for every experiment cell afterward instead of calling
    this again; see the module docstring for why re-running this between
    cells is a problem. `indexed_doc_names` is the corpus-scope allowlist
    the caller resolved (e.g. via `offline.corpus.resolve_pdf_paths`) —
    passed through, not re-derived here, and recorded on the manifest.
    `skipped_pages_by_reason`/`gold_overlap_clean` are likewise passed
    through from the caller's own page-filtering and safety-assertion
    steps — this function doesn't run either itself."""
    metadata_store = PostgresMetadataStore(postgres_dsn)
    dense_index = PersistentDenseIndex(dimension=embedder.dimension)

    if propositions:
        vectors = embedder.encode_passages([p.text for p in propositions])
        for proposition, vector in zip(propositions, vectors):
            dense_index.upsert(proposition.proposition_id, vector)
            metadata_store.upsert(proposition)

    dense_index.save(dense_index_path)
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
    """Reopens persisted state — no LLM call, no re-embedding of anything
    already in the FAISS index."""
    metadata_store = PostgresMetadataStore(postgres_dsn)
    dense_index = PersistentDenseIndex.load(dense_index_path, dimension=dimension)

    sparse_index = SparseIndex()
    for proposition in metadata_store.all():
        sparse_index.upsert(proposition.proposition_id, proposition.text)

    return IndexBundle(dense_index=dense_index, sparse_index=sparse_index, metadata_store=metadata_store)
