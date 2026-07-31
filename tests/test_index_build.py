"""Index build/load orchestration tests -- "index once and persist"."""
from __future__ import annotations

from contracts.models import Confidence, Proposition, PropositionMetadata
from doubles import FakeEmbedder
from offline.indexing.build import build_index, compute_build_id, load_index
from offline.indexing.metadata_store_postgres import PostgresMetadataStore


def _make_proposition(proposition_id: str, text: str) -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="Acme Corp", filing_type="10-Q", filing_date="2024-11-05",
            section="MD&A", page=34, source_uri="acme_10q_2024q3.pdf",
        ),
    )


def test_build_index_persists_dense_and_metadata(clean_postgres, tmp_path):
    embedder = FakeEmbedder(dimension=8)
    props = [
        _make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."),
        _make_proposition("p_2", "Acme Corp net sales for Q3 FY2024 were $8.5 billion."),
    ]

    manifest = build_index(props, embedder, tmp_path / "idx", clean_postgres)

    assert manifest.proposition_count == 2
    assert manifest.embedder_model_name == "fake-embedder"
    assert manifest.embedder_dimension == 8

    bundle = load_index(tmp_path / "idx", clean_postgres, dimension=8)
    assert bundle.dense_index.ids() == {"p_1", "p_2"}
    assert bundle.metadata_store.ids() == {"p_1", "p_2"}


def test_load_index_rebuilds_sparse_index_without_llm(clean_postgres, tmp_path, llm_call_counter):
    embedder = FakeEmbedder(dimension=8)
    props = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]
    build_index(props, embedder, tmp_path / "idx", clean_postgres)

    bundle = load_index(tmp_path / "idx", clean_postgres, dimension=8)

    assert bundle.sparse_index.ids() == {"p_1"}
    assert llm_call_counter.count == 0


def test_compute_build_id_is_deterministic_for_identical_input():
    embedder = FakeEmbedder(dimension=8)
    props = [_make_proposition("p_1", "text a"), _make_proposition("p_2", "text b")]

    assert compute_build_id(props, embedder) == compute_build_id(list(reversed(props)), embedder)


def test_compute_build_id_differs_for_different_propositions():
    embedder = FakeEmbedder(dimension=8)
    props_a = [_make_proposition("p_1", "text a")]
    props_b = [_make_proposition("p_1", "text a"), _make_proposition("p_2", "text b")]

    assert compute_build_id(props_a, embedder) != compute_build_id(props_b, embedder)


def test_two_build_index_runs_over_identical_input_produce_identical_proposition_id_sets(
    clean_postgres, tmp_path
):
    """The literal ask: if indexing were accidentally re-run between
    experiment cells, it must be provably the same build — same
    proposition_id set, same build_id — not silently different propositions
    from a second, independent LLM segmentation pass."""
    embedder = FakeEmbedder(dimension=8)
    props = [
        _make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."),
        _make_proposition("p_2", "Acme Corp net sales for Q3 FY2024 were $8.5 billion."),
    ]

    manifest_first = build_index(props, embedder, tmp_path / "idx_a", clean_postgres)
    ids_first = load_index(tmp_path / "idx_a", clean_postgres, dimension=8).metadata_store.ids()

    # Simulate an accidental re-run over the identical proposition set,
    # against a fresh store (a real re-run would target the same store, but
    # PostgresMetadataStore.upsert is already covered as idempotent by
    # tests/test_metadata_store_postgres.py — what matters here is that the
    # *build* itself, including build_id, is reproducible).
    PostgresMetadataStore(clean_postgres).clear()
    manifest_second = build_index(props, embedder, tmp_path / "idx_b", clean_postgres)
    ids_second = load_index(tmp_path / "idx_b", clean_postgres, dimension=8).metadata_store.ids()

    assert manifest_first.build_id == manifest_second.build_id
    assert ids_first == ids_second == {"p_1", "p_2"}


def test_build_index_with_no_propositions_is_a_no_op(clean_postgres, tmp_path):
    embedder = FakeEmbedder(dimension=8)

    manifest = build_index([], embedder, tmp_path / "idx", clean_postgres)

    assert manifest.proposition_count == 0
    bundle = load_index(tmp_path / "idx", clean_postgres, dimension=8)
    assert bundle.dense_index.ids() == set()
    assert bundle.metadata_store.ids() == set()


def test_build_manifest_records_indexed_doc_names(clean_postgres, tmp_path):
    """Corpus scope must be identical across C0-C4 — recording the indexed
    document set on the manifest makes a scope drift between cells
    detectable, the same way build_id makes a proposition-set drift
    detectable."""
    embedder = FakeEmbedder(dimension=8)
    props = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]

    manifest = build_index(
        props, embedder, tmp_path / "idx", clean_postgres,
        indexed_doc_names=["3M_2018_10K", "ADOBE_2015_10K"],
    )

    assert manifest.indexed_doc_names == ("3M_2018_10K", "ADOBE_2015_10K")


def test_build_manifest_records_skipped_pages_and_gold_overlap_result(clean_postgres, tmp_path):
    embedder = FakeEmbedder(dimension=8)
    props = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]

    manifest = build_index(
        props, embedder, tmp_path / "idx", clean_postgres,
        skipped_pages_by_reason={"risk_factors_item_1a": 30, "cover": 4},
        gold_overlap_clean=True,
    )

    assert manifest.skipped_pages_by_reason == {"risk_factors_item_1a": 30, "cover": 4}
    assert manifest.gold_overlap_clean is True


def test_build_manifest_skipped_pages_and_gold_overlap_default(clean_postgres, tmp_path):
    embedder = FakeEmbedder(dimension=8)

    manifest = build_index([], embedder, tmp_path / "idx", clean_postgres)

    assert manifest.skipped_pages_by_reason == {}
    assert manifest.gold_overlap_clean is None


def test_build_manifest_indexed_doc_names_defaults_to_empty(clean_postgres, tmp_path):
    embedder = FakeEmbedder(dimension=8)

    manifest = build_index([], embedder, tmp_path / "idx", clean_postgres)

    assert manifest.indexed_doc_names == ()
