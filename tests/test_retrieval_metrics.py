"""Retrieval relevance and Hit Rate/MRR/NDCG tests.

The zero-indexing tests are the load-bearing ones here: an off-by-one in
page matching silently depresses every retrieval metric uniformly and reads
as "the system is weak" rather than "the scorer has a bug" — exactly the
failure mode the user flagged.
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
        """evidence_page_num is zero-indexed already — no +1/-1
        translation. Using page 5 (not 0) so an accidental off-by-one would
        produce a visibly wrong 4 or 6, not something that looks coincidentally
        right."""
        evidence = [{"evidence_page_num": 5, "evidence_text": "..."}]

        refs = gold_refs_from_financebench_evidence(evidence, source_uri="acme_10q.pdf")

        assert refs == [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]

    def test_proposition_on_the_gold_page_is_relevant(self):
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        prop = _make_proposition("p_1", source_uri="acme_10q.pdf", page=5)

        assert is_relevant(prop, gold_refs) is True

    def test_proposition_on_the_adjacent_page_is_not_relevant(self):
        """The off-by-one catcher: page 6 is one past the gold page 5. If a
        +1 or -1 crept into either the gold-ref mapping or the parser's page
        numbering, this test — not just the exact-match one above — is what
        would flip and catch it."""
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
        """Integration check: the Layout/Table Parser's real zero-indexed
        page numbering (offline/parsers/layout_parser.py, via
        enumerate(pdf.pages)) and this module's zero-indexed gold-ref
        convention must agree in practice, not just in isolated unit
        assertions. The table lives on the fixture's second physical page
        (page_number == 1)."""
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
    """gold_refs_from_collision_record bridges the FinanceBench-Collision
    schema -- distinct from the stock FinanceBench schema the sibling
    function above reads. Real example, verbatim shape, from
    evaluation/data/financebench_collision_metric_axis.jsonl."""

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
        """A temporal-axis item's comparative-statement evidence spans one
        page but covers two periods -- still one evidence_ref, but a
        multi-year table could plausibly reference more than one page."""
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
        """Same off-by-one concern as the stock-schema sibling test:
        page_num_zero_indexed and Proposition.metadata.page are both
        zero-indexed already -- page 0 must stay page 0."""
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
        """Regression, 2026-07-25: caught running the real 5-document
        retrieval gate -- relevance is page-level (`is_relevant`), and a
        single gold page routinely carries many relevant propositions (a
        real financial statement page has dozens of line items). The old
        IDCG assumed exactly `len(gold_refs)` relevant items -- one per gold
        *page* -- so five relevant propositions from the same one-page
        gold_refs list, all ranking in the top 5, produced NDCG > 1.6 in
        production. A single gold page with several matching propositions,
        all ranked first, must still cap at exactly 1.0 -- that ranking
        genuinely is ideal, not "better than ideal."""
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        ranked = [_make_proposition(f"p_{i}", "acme_10q.pdf", page=5) for i in range(5)]  # 5 props, same gold page

        assert ndcg_at_k(ranked, gold_refs, k=5) == pytest.approx(1.0)

    def test_ndcg_correctly_penalizes_missing_some_same_page_relevant_items_from_top_k(self):
        """Same one-page, multi-proposition scenario, but only some of the
        relevant propositions make the top-k -- NDCG must still reflect
        that as less than ideal, not silently become 1.0 or reuse the old
        len(gold_refs)-based ideal count."""
        gold_refs = [GoldEvidenceRef(source_uri="acme_10q.pdf", page=5)]
        # 3 relevant (page 5) propositions total, but only 1 of them lands
        # in the top-2 -- the ranking that surfaces it first should still
        # score lower than surfacing all 3 first (previous test).
        ranked = [
            _make_proposition("p_1", "acme_10q.pdf", page=5),
            _make_proposition("p_2", "acme_10q.pdf", page=99),
            _make_proposition("p_3", "acme_10q.pdf", page=5),
            _make_proposition("p_4", "acme_10q.pdf", page=5),
        ]

        result = ndcg_at_k(ranked, gold_refs, k=2)

        assert 0.0 < result < 1.0
