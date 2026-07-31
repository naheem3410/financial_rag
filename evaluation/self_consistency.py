"""Self-consistency (k-repeat majority vote) evaluation wrapper. Built
after a real noise-floor measurement (5 independent C1 runs, identical
config/index/model) showed exact-match accuracy is stable at n=1 (0/37
questions flipped their exact-match verdict across runs) but abstention
state is not (5/37 questions flipped between abstained and answered). At
n=37 questions where each is 2.7 accuracy points, that instability
threatens whether a cell-to-cell delta (C1 vs C2) reflects the technique
or a lucky/unlucky draw — this module exists to collapse that jitter
before any cell comparison is trusted.

Two-stage majority vote:
1. Vote on abstain/answered state across all `k` repeats. If a strict
   majority abstain, the question's consensus verdict is "abstained" —
   `exact_match` is not scored (`None`), matching how a single abstained
   run is already scored.
2. If a strict majority answered, vote on `exact_match` (the existing
   by-value comparison, never a string/label match) among only the
   answered repeats. A tie (only possible when exactly 2 of `k` answered)
   is scored conservatively as no-match — an ambiguous case isn't counted
   as a hit — but the tie itself is recorded in `exact_match_vote` so it's
   visible in the report, not silently resolved.

Applied identically to every cell (same `k`, same absence of forced
seeding) so a baseline-vs-treatment comparison isn't reintroducing
sampling-policy noise as a second variable.

Deliberately not best-of-k ("correct if any repeat hits gold") — that
would inflate accuracy by rewarding a lucky draw, the opposite of what
self-consistency is for. This module only ever narrows toward a genuine
majority, never expands toward the most favorable single repeat.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, TypeVar, Union

T = TypeVar("T")


def majority_vote_abstention(abstained_flags: Sequence[bool]) -> tuple[bool, str]:
    """Strict majority; with odd `k` there is never a tie. Even `k` is not
    a supported configuration for this vote (callers use odd `k`, e.g. 3)."""
    n = len(abstained_flags)
    abstain_count = sum(1 for f in abstained_flags if f)
    answered_count = n - abstain_count
    majority_abstained = abstain_count > answered_count
    winner, loser = (abstain_count, answered_count) if majority_abstained else (answered_count, abstain_count)
    label = f"{'abstain' if majority_abstained else 'answered'} {winner}-{loser}"
    return majority_abstained, label


def majority_vote_exact_match(exact_match_flags: Sequence[bool]) -> tuple[bool, str]:
    """Vote among the *answered* repeats only (never the abstained ones —
    those already lost the outer vote in `majority_vote_abstention`).
    A tie (only possible at even count, e.g. 2 of 3 total repeats answered)
    is scored as no-match, conservatively — an ambiguous case does not
    count as a hit — but is labeled "(tied, no consensus)" so it's visible
    in the report rather than silently resolved."""
    n = len(exact_match_flags)
    match_count = sum(1 for f in exact_match_flags if f)
    nomatch_count = n - match_count
    if match_count == nomatch_count:
        return False, f"match {match_count}-{nomatch_count} (tied, no consensus -- scored as no-match)"
    majority_match = match_count > nomatch_count
    winner, loser = (match_count, nomatch_count) if majority_match else (nomatch_count, match_count)
    label = f"{'match' if majority_match else 'no_match'} {winner}-{loser}"
    return majority_match, label


@dataclass
class ConsensusQuestionRecord:
    collision_id: str
    question: str
    verification_status: str
    k: int
    abstained: bool
    abstain_vote: str
    exact_match: Optional[bool]  # None if abstained, or if verification_status != "verified"
    exact_match_vote: Optional[str]  # None if abstained
    total_cost_usd: float
    raw_runs: list[dict[str, Any]]  # every repeat's full underlying record, for transparency


def _load_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    if checkpoint_path.exists():
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))
    return {"completed_runs": {}}  # "{collision_id}::k{i}" -> raw run dict


def _save_checkpoint(checkpoint_path: Path, state: dict[str, Any]) -> None:
    checkpoint_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def aggregate_consensus(
    collision_id: str,
    question: str,
    verification_status: str,
    raw_runs: list[dict[str, Any]],
) -> ConsensusQuestionRecord:
    """Pure aggregation over `k` already-completed raw run records (each
    shaped like `run_c1_gate.C1QuestionRecord.__dict__` /
    `run_c2_gate.C2QuestionRecord.__dict__` — both carry `abstained`,
    `exact_match`, `cost_usd`), applying the two-stage majority vote."""
    k = len(raw_runs)
    abstained_flags = [r["abstained"] for r in raw_runs]
    consensus_abstained, abstain_vote = majority_vote_abstention(abstained_flags)

    exact_match: Optional[bool] = None
    exact_match_vote: Optional[str] = None
    if not consensus_abstained and verification_status == "verified":
        answered_runs = [r for r in raw_runs if not r["abstained"]]
        em_flags = [bool(r["exact_match"]) for r in answered_runs]
        exact_match, exact_match_vote = majority_vote_exact_match(em_flags)

    return ConsensusQuestionRecord(
        collision_id=collision_id,
        question=question,
        verification_status=verification_status,
        k=k,
        abstained=consensus_abstained,
        abstain_vote=abstain_vote,
        exact_match=exact_match,
        exact_match_vote=exact_match_vote,
        total_cost_usd=sum(r["cost_usd"] for r in raw_runs),
        raw_runs=raw_runs,
    )


def run_self_consistency(
    records: Sequence[dict],
    run_one: Callable[[dict, dict, float], Any],  # (record, spend_state, max_spend_usd) -> a raw-run dataclass with .__dict__
    checkpoint_path: Union[str, Path],
    max_spend_usd: float,
    k: int = 3,
) -> list[ConsensusQuestionRecord]:
    """Cell-agnostic: `run_one` is `run_c1_gate.run_c1_question_once` or
    `run_c2_gate.run_c2_question_once`, partially applied over
    `(bundle, embedder, reranker, config)` by the caller — this module
    only owns the k-repeat/checkpoint/majority-vote machinery, not
    retrieval or generation, so C1 and C2 self-consistency share one
    implementation.

    Checkpoints one raw run per `(collision_id, repeat_index)` key, so a
    partially-completed k=3 sweep resumes exactly where it left off
    (including mid-question, e.g. repeat 2 of 3) rather than re-running
    already-completed repeats."""
    checkpoint_path = Path(checkpoint_path)
    state = _load_checkpoint(checkpoint_path)
    spend_state = {"spent": sum(r["cost_usd"] for r in state["completed_runs"].values())}

    results: list[ConsensusQuestionRecord] = []
    for record in records:
        collision_id = record["collision_id"]
        raw_runs: list[dict[str, Any]] = []
        for i in range(k):
            run_key = f"{collision_id}::k{i}"
            if run_key in state["completed_runs"]:
                raw_runs.append(state["completed_runs"][run_key])
                continue

            from offline.pilot import SpendCapExceeded  # local import avoids a cycle at module load

            if spend_state["spent"] >= max_spend_usd:
                raise SpendCapExceeded(
                    f"Spend cap ${max_spend_usd:.2f} reached before starting {run_key!r} "
                    f"(${spend_state['spent']:.4f} spent so far)."
                )

            raw_result = run_one(record, spend_state, max_spend_usd)
            raw_dict = raw_result.__dict__
            raw_runs.append(raw_dict)
            state["completed_runs"][run_key] = raw_dict
            _save_checkpoint(checkpoint_path, state)

        results.append(
            aggregate_consensus(collision_id, record["question"], record["verification_status"], raw_runs)
        )

    return results


def summarize_consensus(results: Sequence[ConsensusQuestionRecord]) -> dict[str, Any]:
    """`exact_match is None` has two different causes under self-consistency
    that `run_c1_gate.summarize()`'s single-run field names would conflate
    if reused as-is: (1) `verification_status != "verified"` (gold
    annotation not yet confirmed), and (2) the consensus vote abstained on
    an already-verified row (nothing to score, not an annotation gap).
    Reporting both under one "n_pending_verification" count would
    misrepresent a real abstention rate as an annotation backlog. Split
    explicitly below."""
    n = len(results)
    unverified = [r for r in results if r.verification_status != "verified"]
    verified = [r for r in results if r.verification_status == "verified"]
    verified_abstained = [r for r in verified if r.abstained]
    scored = [r for r in verified if r.exact_match is not None]
    negative_rejections = sum(1 for r in results if r.abstained)
    tied_exact_match = [r.collision_id for r in results if r.exact_match_vote and "tied" in r.exact_match_vote]
    split_abstain_votes = [r.collision_id for r in results if r.abstain_vote.split()[-1].split("-")[1] != "0"]
    return {
        "n_questions": n,
        "k": results[0].k if results else None,
        "n_verified": len(verified),
        "n_unverified_annotation_pending": len(unverified),
        "n_verified_but_consensus_abstained": len(verified_abstained),
        "n_scored": len(scored),
        "exact_match_accuracy_verified_and_answered_only": (sum(1 for r in scored if r.exact_match) / len(scored)) if scored else None,
        "negative_rejection_rate": negative_rejections / n if n else None,
        "total_cost_usd": sum(r.total_cost_usd for r in results),
        "questions_with_non_unanimous_abstain_vote": split_abstain_votes,
        "questions_with_tied_exact_match_vote": tied_exact_match,
    }
