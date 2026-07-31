"""Retrieval-quality gate wiring tests. Uses fake dense/sparse/metadata stores and a fake
reranker (no real FAISS/Postgres/models) — these test that the pipeline
wiring (dense+sparse union -> RRF -> rerank -> score) is correct, not the
already-tested pieces (fusion math, metrics math) it composes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from doubles import FakeEmbedder

from contracts.models import Confidence, Proposition, PropositionMetadata
from evaluation.run_retrieval_gate import evaluate_question, retrieve_fused, run_gate, summarize


def _make_proposition(proposition_id: str, source_uri: str, page: int, text: str) -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="Acme Corp", filing_type="10-K", filing_date="2024-01-01",
            section="", page=page, source_uri=source_uri,
        ),
    )


class _FakeDenseIndex:
    def __init__(self, hits_by_query_vector_signature):
        # In these tests the embedder is deterministic per text, so we key
        # fake hits by a caller-supplied ordering instead of real vectors.
        self._hits = hits_by_query_vector_signature

    def search(self, query_vector, k):
        return self._hits[: k]


class _FakeSparseIndex:
    def __init__(self, hits_by_query_text):
        self._hits_by_query_text = hits_by_query_text

    def search(self, query_text, k):
        return self._hits_by_query_text.get(query_text, [])[:k]


class _FakeMetadataStore:
    def __init__(self, propositions_by_id):
        self._by_id = propositions_by_id

    def get(self, proposition_id):
        return self._by_id[proposition_id]


class _FakeBundle:
    def __init__(self, dense_index, sparse_index, metadata_store):
        self.dense_index = dense_index
        self.sparse_index = sparse_index
        self.metadata_store = metadata_store


class _FakeReranker:
    """Identity reranker -- returns the input order truncated to k, so
    these tests isolate the retrieval/fusion wiring from reranking logic
    (already covered by tests/test_reranker.py)."""

    def rerank(self, sq, props, k):
        return list(props[:k])


def test_evaluate_question_finds_the_gold_page_via_dense_arm():
    gold_prop = _make_proposition("p_gold", "3M_2018_10K", page=57, text="Net sales were $32.2 billion.")
    other_prop = _make_proposition("p_other", "3M_2018_10K", page=10, text="Unrelated narrative text.")
    metadata_store = _FakeMetadataStore({"p_gold": gold_prop, "p_other": other_prop})
    dense_index = _FakeDenseIndex([("p_gold", 0.95), ("p_other", 0.10)])
    sparse_index = _FakeSparseIndex({})
    bundle = _FakeBundle(dense_index, sparse_index, metadata_store)

    record = {
        "collision_id": "fbc_test_0001",
        "question": "What were 3M's net sales in FY2018?",
        "source": {"primary_doc": "3M_2018_10K", "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 57}]},
    }

    result = evaluate_question(
        record, bundle, FakeEmbedder(dimension=8), _FakeReranker(), top_k=10, rerank_top_k=5, time_weighting=False
    )

    assert result.hit_rate_fused == 1.0
    assert result.hit_rate_reranked == 1.0
    assert result.reciprocal_rank_reranked > 0.0  # found, somewhere in the ranked list
    assert result.ndcg_reranked > 0.0


def test_evaluate_question_misses_when_gold_page_never_retrieved():
    other_prop = _make_proposition("p_other", "3M_2018_10K", page=10, text="Unrelated narrative text.")
    metadata_store = _FakeMetadataStore({"p_other": other_prop})
    dense_index = _FakeDenseIndex([("p_other", 0.5)])
    sparse_index = _FakeSparseIndex({})
    bundle = _FakeBundle(dense_index, sparse_index, metadata_store)

    record = {
        "collision_id": "fbc_test_0002",
        "question": "What were 3M's net sales in FY2018?",
        "source": {"primary_doc": "3M_2018_10K", "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 57}]},
    }

    result = evaluate_question(
        record, bundle, FakeEmbedder(dimension=8), _FakeReranker(), top_k=10, rerank_top_k=5, time_weighting=False
    )

    assert result.hit_rate_fused == 0.0
    assert result.hit_rate_reranked == 0.0
    assert result.reciprocal_rank_reranked == 0.0
    assert result.ndcg_reranked == 0.0


def test_dense_and_sparse_hits_are_unioned_not_intersected():
    """A proposition found by only ONE arm (e.g. sparse BM25 exact-term
    match, zero dense similarity) must still be a fusion candidate --
    unioning, not intersecting, the two arms' top-k id sets."""
    gold_prop = _make_proposition("p_gold", "3M_2018_10K", page=57, text="Net sales were $32.2 billion.")
    metadata_store = _FakeMetadataStore({"p_gold": gold_prop})
    dense_index = _FakeDenseIndex([])  # gold NOT found by the dense arm at all
    sparse_index = _FakeSparseIndex({"What were 3M's net sales in FY2018?": [("p_gold", 4.2)]})
    bundle = _FakeBundle(dense_index, sparse_index, metadata_store)

    record = {
        "collision_id": "fbc_test_0003",
        "question": "What were 3M's net sales in FY2018?",
        "source": {"primary_doc": "3M_2018_10K", "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 57}]},
    }

    result = evaluate_question(
        record, bundle, FakeEmbedder(dimension=8), _FakeReranker(), top_k=10, rerank_top_k=5, time_weighting=False
    )

    assert result.hit_rate_fused == 1.0


def test_run_gate_and_summarize_average_across_questions():
    gold_prop = _make_proposition("p_gold", "3M_2018_10K", page=57, text="Net sales were $32.2 billion.")
    other_prop = _make_proposition("p_other", "3M_2018_10K", page=10, text="Unrelated narrative text.")
    metadata_store = _FakeMetadataStore({"p_gold": gold_prop, "p_other": other_prop})
    bundle = _FakeBundle(
        _FakeDenseIndex([("p_gold", 0.95), ("p_other", 0.10)]), _FakeSparseIndex({}), metadata_store
    )
    records = [
        {
            "collision_id": "fbc_test_hit",
            "question": "Q1",
            "source": {"primary_doc": "3M_2018_10K", "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 57}]},
        },
        {
            "collision_id": "fbc_test_miss",
            "question": "Q2",
            "source": {"primary_doc": "3M_2018_10K", "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 999}]},
        },
    ]

    results = run_gate(records, bundle, FakeEmbedder(dimension=8), _FakeReranker(), top_k=10, rerank_top_k=5, time_weighting=False)
    summary = summarize(results)

    assert summary["n_questions"] == 2
    assert summary["hit_rate_at_rerank_k"] == 0.5  # one hit, one miss


def test_retrieve_fused_candidate_order_is_independent_of_hash_seed():
    """Regression, 2026-07-25: `retrieve_fused` used to build its candidate
    set via `set(dense_hits) | set(sparse_hits)`. Python randomizes string
    hash seeds per process (PYTHONHASHSEED unset by default), so that
    union's iteration order differed between process runs; RRF's *stable*
    sort then broke any tied dense/sparse score according to that
    input order, so the exact same question against the exact same index
    could flip between a rerank hit and a miss depending on nothing but
    which process happened to run it. Confirmed directly: running the real
    fbc_metric_pri_0052 question against the real index in three separate
    `python -c ...` invocations produced hit_rate_reranked of 1.0, 0.0, 1.0.

    This test forces two different hash seeds via subprocess and asserts
    the fused candidate order comes out identical either way -- the actual
    regression this class of bug produces, not just "sorted() is
    deterministic" (which would be true regardless of whether the fix was
    applied).
    """
    script = """
import sys
sys.path.insert(0, r'{repo_root}')
from evaluation.run_retrieval_gate import retrieve_fused

class FakeEmbedder:
    def encode_queries(self, texts):
        return [[1.0, 0.0]]

class FakeDenseIndex:
    def search(self, query_vector, k):
        # Several ids tied at the same score -- exactly the scenario a
        # stable sort resolves using input (iteration) order.
        return [(f"p_{{i}}", 0.5) for i in range(8)][:k]

class FakeSparseIndex:
    def search(self, query_text, k):
        return [(f"p_{{i}}", 0.5) for i in range(3, 11)][:k]

class FakeMetadataStore:
    def get(self, proposition_id):
        raise AssertionError("not needed for this test -- fused order is read before .get()")

import evaluation.run_retrieval_gate as m

def fake_get(pid):
    from contracts.models import Confidence, Proposition, PropositionMetadata
    return Proposition(
        proposition_id=pid, text="t", provenance_tier="retrieved", ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="C", filing_type="10-K", filing_date="2024-01-01", section="", page=0, source_uri="doc"),
    )

class Bundle:
    dense_index = FakeDenseIndex()
    sparse_index = FakeSparseIndex()
    class metadata_store:
        get = staticmethod(fake_get)

fused = retrieve_fused("q", Bundle(), FakeEmbedder(), top_k=10, time_weighting=False)
print(",".join(f.proposition.proposition_id for f in fused))
""".format(repo_root=str(Path(__file__).resolve().parents[1]))

    outputs = []
    for seed in ("0", "1"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout.strip())

    assert outputs[0] == outputs[1]
