"""Rank fusion + time-aware weighting tests."""
from __future__ import annotations

from contracts.models import Confidence, Proposition, PropositionMetadata
from online.retrieval.fusion import apply_time_weighting, reciprocal_rank_fusion
from online.retrieval.hybrid import ScoredProposition


def _make_proposition(proposition_id: str, filing_date: str = "2024-11-05") -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text="Acme Corp operating margin for Q3 FY2024 was 18.2 percent.",
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="Acme Corp", filing_type="10-Q", filing_date=filing_date,
            section="MD&A", page=34, source_uri="acme_10q.pdf",
        ),
    )


class TestReciprocalRankFusion:
    def test_empty_input_returns_empty(self):
        assert reciprocal_rank_fusion([]) == []

    def test_top_of_both_arms_ranks_first(self):
        p1, p2, p3 = _make_proposition("p_1"), _make_proposition("p_2"), _make_proposition("p_3")
        scored = [
            ScoredProposition(proposition=p1, dense_score=0.9, sparse_score=5.0),
            ScoredProposition(proposition=p2, dense_score=0.1, sparse_score=1.0),
            ScoredProposition(proposition=p3, dense_score=0.5, sparse_score=3.0),
        ]

        fused = reciprocal_rank_fusion(scored)

        assert fused[0].proposition.proposition_id == "p_1"
        assert fused[-1].proposition.proposition_id == "p_2"

    def test_a_result_strong_on_only_one_arm_still_beats_weak_on_both(self):
        """A doc strong on one arm should beat one that's mediocre on both.
        That's the whole reason we use RRF instead of averaging raw scores."""
        p_strong_dense = _make_proposition("p_strong_dense")
        p_strong_sparse = _make_proposition("p_strong_sparse")
        p_mediocre = _make_proposition("p_mediocre")
        scored = [
            ScoredProposition(proposition=p_strong_dense, dense_score=1.0, sparse_score=0.1),
            ScoredProposition(proposition=p_strong_sparse, dense_score=0.1, sparse_score=1.0),
            ScoredProposition(proposition=p_mediocre, dense_score=0.5, sparse_score=0.5),
        ]

        fused = reciprocal_rank_fusion(scored)
        ranked_ids = [f.proposition.proposition_id for f in fused]

        # mediocre is rank 2 on both arms, worse than either single-arm leader's rank-1 finish
        assert ranked_ids.index("p_mediocre") > ranked_ids.index("p_strong_dense")
        assert ranked_ids.index("p_mediocre") > ranked_ids.index("p_strong_sparse")

    def test_rrf_score_is_symmetric_in_the_two_arms(self):
        p1, p2 = _make_proposition("p_1"), _make_proposition("p_2")
        scored_a = [
            ScoredProposition(proposition=p1, dense_score=0.9, sparse_score=0.1),
            ScoredProposition(proposition=p2, dense_score=0.1, sparse_score=0.9),
        ]
        scored_b = [
            ScoredProposition(proposition=p1, dense_score=0.1, sparse_score=0.9),
            ScoredProposition(proposition=p2, dense_score=0.9, sparse_score=0.1),
        ]

        fused_a = {f.proposition.proposition_id: f.rrf_score for f in reciprocal_rank_fusion(scored_a)}
        fused_b = {f.proposition.proposition_id: f.rrf_score for f in reciprocal_rank_fusion(scored_b)}

        assert fused_a["p_1"] == fused_b["p_2"]
        assert fused_a["p_2"] == fused_b["p_1"]


class TestTimeWeighting:
    def test_disabled_returns_input_unchanged_in_order(self):
        p1, p2 = _make_proposition("p_1", "2020-01-01"), _make_proposition("p_2", "2024-01-01")
        scored = [
            ScoredProposition(proposition=p1, dense_score=0.9, sparse_score=0.9),
            ScoredProposition(proposition=p2, dense_score=0.1, sparse_score=0.1),
        ]
        fused = reciprocal_rank_fusion(scored)

        result = apply_time_weighting(fused, enabled=False)

        assert [f.proposition.proposition_id for f in result] == [f.proposition.proposition_id for f in fused]

    def test_enabled_never_overrides_a_much_stronger_older_match(self):
        """RRF is rank-based, so "much stronger" means a rank gap, not a score gap.
        Five candidates give us a rank-1-vs-rank-5 gap the recency nudge can't overcome."""
        p_old_strong = _make_proposition("p_old_strong", "2015-01-01")
        p_new_weak = _make_proposition("p_new_weak", "2024-01-01")
        fillers = [_make_proposition(f"p_filler_{i}", "2020-01-01") for i in range(3)]
        scored = [
            ScoredProposition(proposition=p_old_strong, dense_score=1.0, sparse_score=1.0),  # rank 1 on both arms
            ScoredProposition(proposition=p_new_weak, dense_score=0.0, sparse_score=0.0),  # rank 5 on both arms
            ScoredProposition(proposition=fillers[0], dense_score=0.8, sparse_score=0.8),
            ScoredProposition(proposition=fillers[1], dense_score=0.6, sparse_score=0.6),
            ScoredProposition(proposition=fillers[2], dense_score=0.4, sparse_score=0.4),
        ]
        fused = reciprocal_rank_fusion(scored)

        result = apply_time_weighting(fused, enabled=True)

        assert result[0].proposition.proposition_id == "p_old_strong"

    def test_enabled_breaks_a_genuine_tie_toward_the_more_recent_result(self):
        p_old = _make_proposition("p_old", "2015-01-01")
        p_new = _make_proposition("p_new", "2024-01-01")
        # each gets a rank-1 on one arm and rank-2 on the other, so the RRF
        # scores tie regardless of list order (not just a stable-sort artifact)
        scored = [
            ScoredProposition(proposition=p_old, dense_score=0.9, sparse_score=0.1),
            ScoredProposition(proposition=p_new, dense_score=0.1, sparse_score=0.9),
        ]
        fused = reciprocal_rank_fusion(scored)
        assert fused[0].rrf_score == fused[1].rrf_score  # confirm the tie exists first

        result = apply_time_weighting(fused, enabled=True)

        assert result[0].proposition.proposition_id == "p_new"

    def test_empty_input_returns_empty(self):
        assert apply_time_weighting([], enabled=True) == []
