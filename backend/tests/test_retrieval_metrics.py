"""Retrieval relevance and Hit Rate/MRR/NDCG tests.
Zero-indexing tests are load-bearing: an off-by-one here silently reads as "system is weak" not "scorer has a bug."
"""
from __future__ import annotations

from pathlib import Path

import pytest

from contracts.models import Confidence, Proposition, PropositionMetadata
from evaluation.retrieval_metrics import (
    GoldEvidenceRef,
    gold_refs_from_collision_record,
    gold_refs_from_financebench_evidence,
    hit_rate_at_k,
    is_relevant,
    ndcg_at_k,
    reciprocal_rank,
)
from offline.parsers.layout_parser import parse_document

PDF_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "path_a" / "sample.pdf"


def _make_proposition(proposition_id: str, source_uri: str, page: int) -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text="Acme Corp operating margin for Q3 FY2024 was 18.2 percent.",
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="Acme Corp", filing_type="10-Q", filing_date="2024-11-05",
            section="MD&A", page=page, source_uri=source_uri,
        ),
    )


class TestZeroIndexedPageConvention:
    def test_gold_refs_from_financebench_evidence_preserves_page_num_unchanged(self):
        """evidence_page_num is already zero-indexed, no +1/-1 translation.
        Page 5, not 0, so an off-by-one produces a visibly wrong 4 or 6."""
        evidence = [{"evidence_page_num": 5, "evidence_text": "..."}]

        refs = gold_refs_from_financebench_evidence(evidence, source_uri="acme_10q.pdf")

        assert refs == [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]

    def test_proposition_on_the_gold_page_is_relevant(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        prop = _make_proposition("p_1", source_uri="acme_10q.pdf", page=5)

        assert is_relevant(prop, gold_refs) is True

    def test_proposition_on_the_adjacent_page_is_not_relevant(self):
        """Page 6 is one past the gold page 5. This is the test that would
        catch a stray +1/-1 in the gold-ref mapping or parser's page numbering."""
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        prop_before = _make_proposition("p_1", source_uri="acme_10q.pdf", page=4)
        prop_after = _make_proposition("p_2", source_uri="acme_10q.pdf", page=6)

        assert is_relevant(prop_before, gold_refs) is False
        assert is_relevant(prop_after, gold_refs) is False

    def test_proposition_on_matching_page_but_wrong_document_is_not_relevant(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        prop = _make_proposition("p_1", source_uri="beta_10q.pdf", page=5)

        assert is_relevant(prop, gold_refs) is False

    def test_real_pdf_parser_page_numbering_agrees_with_gold_ref_convention(self):
        """Real parser page numbering and this module's gold-ref convention
        must agree in practice, not just in isolated unit tests. Table is on physical page 2 (page_number == 1)."""
        parsed = parse_document(PDF_FIXTURE)
        table_page = next(p for p in parsed.pages if p.tables)

        assert table_page.page_number == 1

        gold_refs = gold_refs_from_financebench_evidence(
            [{"evidence_page_num": 1, "evidence_text": "Operating margin 18.2%"}],
            source_uri="sample.pdf",
        )
        prop_on_table_page = _make_proposition("p_1", source_uri="sample.pdf", page=table_page.page_number)
        prop_on_prose_page = _make_proposition("p_2", source_uri="sample.pdf", page=0)

        assert is_relevant(prop_on_table_page, gold_refs) is True
        assert is_relevant(prop_on_prose_page, gold_refs) is False


class TestCollisionSchemaGoldRefs:
    """Bridges the FinanceBench-Collision schema, distinct from the stock
    schema the sibling function reads. Shapes taken verbatim from financebench_collision_metric_axis.jsonl."""

    def test_null_doc_name_resolves_to_primary_doc(self):
        record = {
            "source": {
                "primary_doc": "3M_2018_10K",
                "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 59}],
            }
        }

        assert gold_refs_from_collision_record(record) == [
            GoldEvidenceRef(source_uri="3M_2018_10K", page=59)
        ]

    def test_explicit_doc_name_overrides_primary_doc(self):
        record = {
            "source": {
                "primary_doc": "3M_2018_10K",
                "evidence_refs": [{"doc_name": "3M_2017_10K", "page_num_zero_indexed": 12}],
            }
        }

        assert gold_refs_from_collision_record(record) == [
            GoldEvidenceRef(source_uri="3M_2017_10K", page=12)
        ]

    def test_multiple_evidence_refs_produce_multiple_gold_refs(self):
        """A comparative-statement evidence_ref can list more than one page
        for a multi-year table, even though it covers one temporal-axis item."""
        record = {
            "source": {
                "primary_doc": "3M_2022_10K",
                "evidence_refs": [
                    {"doc_name": None, "page_num_zero_indexed": 47},
                    {"doc_name": None, "page_num_zero_indexed": 49},
                ],
            }
        }

        assert gold_refs_from_collision_record(record) == [
            GoldEvidenceRef(source_uri="3M_2022_10K", page=47),
            GoldEvidenceRef(source_uri="3M_2022_10K", page=49),
        ]

    def test_no_plus_one_minus_one_adjustment(self):
        """Same off-by-one concern as the stock-schema sibling test.
        Both sides are already zero-indexed, so page 0 must stay page 0."""
        record = {
            "source": {"primary_doc": "ADOBE_2016_10K", "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 0}]}
        }

        assert gold_refs_from_collision_record(record)[0].page == 0


class TestHitRateMRRNDCG:
    def test_hit_rate_at_k_true_when_a_relevant_result_is_within_k(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        ranked = [
            _make_proposition("p_1", "acme_10q.pdf", page=99),
            _make_proposition("p_2", "acme_10q.pdf", page=5),
        ]

        assert hit_rate_at_k(ranked, gold_refs, k=2) == 1.0
        assert hit_rate_at_k(ranked, gold_refs, k=1) == 0.0  # relevant result is rank 2, outside k=1

    def test_hit_rate_at_k_false_when_nothing_relevant(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        ranked = [_make_proposition("p_1", "acme_10q.pdf", page=99)]

        assert hit_rate_at_k(ranked, gold_refs, k=5) == 0.0

    def test_reciprocal_rank_of_first_relevant_position(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        ranked = [
            _make_proposition("p_1", "acme_10q.pdf", page=99),
            _make_proposition("p_2", "acme_10q.pdf", page=99),
            _make_proposition("p_3", "acme_10q.pdf", page=5),  # rank 3
        ]

        assert reciprocal_rank(ranked, gold_refs) == 1.0 / 3

    def test_reciprocal_rank_is_zero_when_nothing_relevant(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        ranked = [_make_proposition("p_1", "acme_10q.pdf", page=99)]

        assert reciprocal_rank(ranked, gold_refs) == 0.0

    def test_ndcg_is_one_when_relevant_result_ranks_first(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        ranked = [_make_proposition("p_1", "acme_10q.pdf", page=5)]

        assert ndcg_at_k(ranked, gold_refs, k=1) == 1.0

    def test_ndcg_is_lower_when_relevant_result_ranks_later(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        ranked_first = [
            _make_proposition("p_1", "acme_10q.pdf", page=5),
            _make_proposition("p_2", "acme_10q.pdf", page=99),
        ]
        ranked_second = [
            _make_proposition("p_1", "acme_10q.pdf", page=99),
            _make_proposition("p_2", "acme_10q.pdf", page=5),
        ]

        assert ndcg_at_k(ranked_first, gold_refs, k=2) > ndcg_at_k(ranked_second, gold_refs, k=2)

    def test_ndcg_is_zero_when_nothing_relevant(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        ranked = [_make_proposition("p_1", "acme_10q.pdf", page=99)]

        assert ndcg_at_k(ranked, gold_refs, k=5) == 0.0

    def test_ndcg_handles_empty_gold_refs_without_dividing_by_zero(self):
        ranked = [_make_proposition("p_1", "acme_10q.pdf", page=99)]

        assert ndcg_at_k(ranked, [], k=5) == 0.0

    def test_ndcg_never_exceeds_one_when_multiple_relevant_propositions_share_a_gold_page(self):
        """Regression 2026-07-25: old IDCG assumed one relevant item per gold
        page, so 5 relevant props from one gold page produced NDCG > 1.6 in production. Must cap at 1.0."""
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        ranked = [_make_proposition(f"p_{i}", "acme_10q.pdf", page=5) for i in range(5)]

        assert ndcg_at_k(ranked, gold_refs, k=5) == pytest.approx(1.0)

    def test_ndcg_correctly_penalizes_missing_some_same_page_relevant_items_from_top_k(self):
        """Same multi-proposition scenario, but only some make the top-k.
        NDCG must reflect that as less than ideal, not silently round up to 1.0."""
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        # only 1 of 3 relevant props lands in top-2, should score lower than
        # surfacing all 3 first (previous test)
        ranked = [
            _make_proposition("p_1", "acme_10q.pdf", page=5),
            _make_proposition("p_2", "acme_10q.pdf", page=99),
            _make_proposition("p_3", "acme_10q.pdf", page=5),
            _make_proposition("p_4", "acme_10q.pdf", page=5),
        ]

        result = ndcg_at_k(ranked, gold_refs, k=2)

        assert 0.0 < result < 1.0
