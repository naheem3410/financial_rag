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


def test_load_index_loads_the_saved_sparse_index_without_llm(clean_postgres, tmp_path, llm_call_counter):
    embedder = FakeEmbedder(dimension=8)
    props = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]
    build_index(props, embedder, tmp_path / "idx", clean_postgres)

    bundle = load_index(tmp_path / "idx", clean_postgres, dimension=8)

    assert bundle.sparse_index.ids() == {"p_1"}
    assert llm_call_counter.count == 0


def test_build_index_saves_a_keyword_index_file_next_to_the_dense_one(clean_postgres, tmp_path):
    """Step 1.3: nothing should need rebuilding from Postgres at startup."""
    embedder = FakeEmbedder(dimension=8)
    props = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]

    build_index(props, embedder, tmp_path / "idx", clean_postgres)

    assert (tmp_path / "idx.sparse.json").exists()


def test_load_index_reads_the_saved_keyword_file_not_postgres_text(clean_postgres, tmp_path):
    """If loading truly reads the saved file, editing the database's text
    afterward (without rebuilding) must not change what search sees."""
    embedder = FakeEmbedder(dimension=8)
    props = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]
    build_index(props, embedder, tmp_path / "idx", clean_postgres)

    store = PostgresMetadataStore(clean_postgres)
    changed = props[0].model_copy(update={"text": "totally different words now"})
    store.upsert(changed)
    store.close()

    bundle = load_index(tmp_path / "idx", clean_postgres, dimension=8)

    assert bundle.sparse_index.search("operating margin", k=1)[0][0] == "p_1"  # still the old text's words


def test_load_index_self_heals_a_build_from_before_this_step_existed(clean_postgres, tmp_path):
    """A legacy build has no .sparse.json file yet. The first load rebuilds
    it from Postgres (the one slow time) and saves it, so every load after
    that is fast -- this never blocks the app from starting."""
    embedder = FakeEmbedder(dimension=8)
    props = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]
    build_index(props, embedder, tmp_path / "idx", clean_postgres)
    (tmp_path / "idx.sparse.json").unlink()  # simulate a build made before step 1.3

    bundle = load_index(tmp_path / "idx", clean_postgres, dimension=8)

    assert bundle.sparse_index.ids() == {"p_1"}
    assert (tmp_path / "idx.sparse.json").exists()  # healed for next time


def test_load_index_self_heals_a_corrupt_keyword_index_file(clean_postgres, tmp_path):
    embedder = FakeEmbedder(dimension=8)
    props = [_make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.")]
    build_index(props, embedder, tmp_path / "idx", clean_postgres)
    (tmp_path / "idx.sparse.json").write_text("not valid json {{{", encoding="utf-8")

    bundle = load_index(tmp_path / "idx", clean_postgres, dimension=8)  # does not raise

    assert bundle.sparse_index.ids() == {"p_1"}


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
    """A re-run between experiment cells must be provably the same build
    (same ids, same build_id), not a second independent LLM segmentation pass."""
    embedder = FakeEmbedder(dimension=8)
    props = [
        _make_proposition("p_1", "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."),
        _make_proposition("p_2", "Acme Corp net sales for Q3 FY2024 were $8.5 billion."),
    ]

    manifest_first = build_index(props, embedder, tmp_path / "idx_a", clean_postgres)
    ids_first = load_index(tmp_path / "idx_a", clean_postgres, dimension=8).metadata_store.ids()

    # fresh store here, not the same one -- upsert idempotency is already
    # covered elsewhere; this checks build_id reproducibility specifically
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
    """Recording indexed doc names makes corpus scope drift between C0-C4
    detectable, same as build_id does for proposition-set drift."""
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
