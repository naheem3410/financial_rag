"""Self-consistency majority-vote tests. Pure functions, no LLM, no I/O. Must PASS.
See evaluation/self_consistency.py for the noise-floor finding that motivated this.
"""
from __future__ import annotations

from evaluation.self_consistency import (
    ConsensusQuestionRecord,
    aggregate_consensus,
    majority_vote_abstention,
    majority_vote_exact_match,
    summarize_consensus,
)


# --- majority_vote_abstention ---


def test_unanimous_abstain():
    assert majority_vote_abstention([True, True, True]) == (True, "abstain 3-0")


def test_unanimous_answered():
    assert majority_vote_abstention([False, False, False]) == (False, "answered 3-0")


def test_split_2_1_abstain_wins():
    assert majority_vote_abstention([True, True, False]) == (True, "abstain 2-1")


def test_split_2_1_answered_wins():
    assert majority_vote_abstention([True, False, False]) == (False, "answered 2-1")


# --- majority_vote_exact_match ---


def test_exact_match_unanimous_match():
    assert majority_vote_exact_match([True, True, True]) == (True, "match 3-0")


def test_exact_match_unanimous_no_match():
    assert majority_vote_exact_match([False, False, False]) == (False, "no_match 3-0")


def test_exact_match_split_2_1_match_wins():
    assert majority_vote_exact_match([True, True, False]) == (True, "match 2-1")


def test_exact_match_split_2_1_no_match_wins():
    assert majority_vote_exact_match([True, False, False]) == (False, "no_match 2-1")


def test_exact_match_tie_at_n2_scored_conservatively_as_no_match():
    """Only possible when exactly 2 of k repeats answered.
    Must not silently pick a side, must record the tie."""
    result, label = majority_vote_exact_match([True, False])
    assert result is False
    assert "tied" in label
    assert "1-1" in label


def test_exact_match_n2_unanimous():
    assert majority_vote_exact_match([True, True]) == (True, "match 2-0")


def test_exact_match_n1_single_answered_repeat():
    assert majority_vote_exact_match([True]) == (True, "match 1-0")
    assert majority_vote_exact_match([False]) == (False, "no_match 1-0")


# --- aggregate_consensus ---


def _raw(abstained: bool, exact_match, cost_usd: float = 0.001) -> dict:
    return {"abstained": abstained, "exact_match": exact_match, "cost_usd": cost_usd, "answer_text": "x"}


def test_aggregate_consensus_answered_majority_votes_exact_match():
    raw_runs = [_raw(False, True), _raw(False, True), _raw(False, False)]
    result = aggregate_consensus("fbc_x_0001", "q", "verified", raw_runs)

    assert result.abstained is False
    assert result.abstain_vote == "answered 3-0"
    assert result.exact_match is True
    assert result.exact_match_vote == "match 2-1"
    assert result.total_cost_usd == 0.003
    assert result.k == 3


def test_aggregate_consensus_abstain_majority_does_not_score_exact_match():
    """Majority abstain -> exact_match must be None, not False.
    Nothing to score, not a scored miss."""
    raw_runs = [_raw(True, None), _raw(True, None), _raw(False, True)]
    result = aggregate_consensus("fbc_metric_pri_0061", "q", "verified", raw_runs)

    assert result.abstained is True
    assert result.abstain_vote == "abstain 2-1"
    assert result.exact_match is None
    assert result.exact_match_vote is None


def test_aggregate_consensus_unverified_row_never_scores_exact_match_even_if_answered():
    raw_runs = [_raw(False, None), _raw(False, None), _raw(False, None)]
    result = aggregate_consensus("fbc_x_0002", "q", "needs_human_verification", raw_runs)

    assert result.abstained is False
    assert result.exact_match is None
    assert result.exact_match_vote is None


def test_aggregate_consensus_only_answered_repeats_vote_on_exact_match():
    """One abstained repeat must not count as a 'no_match' vote.
    It's excluded from the exact_match sub-vote entirely."""
    raw_runs = [_raw(True, None), _raw(False, True), _raw(False, True)]
    result = aggregate_consensus("fbc_x_0003", "q", "verified", raw_runs)

    assert result.abstained is False  # 2 answered vs 1 abstained -> answered wins
    assert result.exact_match is True
    assert result.exact_match_vote == "match 2-0"  # only the 2 answered repeats voted


# --- summarize_consensus ---


def test_summarize_consensus_flags_non_unanimous_and_tied_questions():
    unanimous = aggregate_consensus("fbc_a", "q", "verified", [_raw(False, True)] * 3)
    split_abstain = aggregate_consensus("fbc_b", "q", "verified", [_raw(True, None), _raw(True, None), _raw(False, True)])
    tied = aggregate_consensus(
        "fbc_c", "q", "verified", [_raw(True, None), _raw(False, True), _raw(False, False)]
    )

    summary = summarize_consensus([unanimous, split_abstain, tied])

    assert summary["n_questions"] == 3
    assert summary["k"] == 3
    assert "fbc_b" in summary["questions_with_non_unanimous_abstain_vote"]
    assert "fbc_a" not in summary["questions_with_non_unanimous_abstain_vote"]
    assert "fbc_c" in summary["questions_with_tied_exact_match_vote"]


def test_summarize_consensus_distinguishes_unverified_from_consensus_abstained():
    """exact_match is None has two different causes: unverified annotation vs
    genuine consensus abstain on a verified row. Must be counted separately."""
    unverified_row = aggregate_consensus("fbc_unverified", "q", "needs_human_verification", [_raw(False, None)] * 3)
    verified_abstained_row = aggregate_consensus("fbc_abstained", "q", "verified", [_raw(True, None)] * 3)
    verified_scored_row = aggregate_consensus("fbc_scored", "q", "verified", [_raw(False, True)] * 3)

    summary = summarize_consensus([unverified_row, verified_abstained_row, verified_scored_row])

    assert summary["n_verified"] == 2
    assert summary["n_unverified_annotation_pending"] == 1
    assert summary["n_verified_but_consensus_abstained"] == 1
    assert summary["n_scored"] == 1
    assert summary["exact_match_accuracy_verified_and_answered_only"] == 1.0
