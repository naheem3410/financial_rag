"""k-repeat majority-vote wrapper for self-consistency evaluation.

Noise-floor check (5x C1 reruns) showed exact-match is stable at n=1 but
abstention flips on 5/37 questions -- enough to fake a cell delta, hence this.
Two-stage vote: abstain/answered first, then exact_match among answered only
(ties score as no-match but get flagged). Same k across cells, never best-of-k.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, TypeVar, Union

T = TypeVar("T")


def majority_vote_abstention(abstained_flags: Sequence[bool]) -> tuple[bool, str]:
    """Strict majority, no ties at odd k. Callers always use odd k (e.g. 3)."""
    n = len(abstained_flags)
    abstain_count = sum(1 for f in abstained_flags if f)
    answered_count = n - abstain_count
    majority_abstained = abstain_count > answered_count
    winner, loser = (abstain_count, answered_count) if majority_abstained else (answered_count, abstain_count)
    label = f"{'abstain' if majority_abstained else 'answered'} {winner}-{loser}"
    return majority_abstained, label


def majority_vote_exact_match(exact_match_flags: Sequence[bool]) -> tuple[bool, str]:
    """Votes only among answered repeats. A tie (even count) scores as
    no-match but is labeled tied so it shows up in the report."""
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
    exact_match: Optional[bool]  # None if abstained or unverified
    exact_match_vote: Optional[str]  # None if abstained
    total_cost_usd: float
    raw_runs: list[dict[str, Any]]  # full record per repeat


def _load_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    if checkpoint_path.exists():
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))
    return {"completed_runs": {}}  # keyed "{collision_id}::k{i}"


def _save_checkpoint(checkpoint_path: Path, state: dict[str, Any]) -> None:
    checkpoint_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def aggregate_consensus(
    collision_id: str,
    question: str,
    verification_status: str,
    raw_runs: list[dict[str, Any]],
) -> ConsensusQuestionRecord:
    """Aggregates k already-completed raw runs (C1/C2QuestionRecord dicts)
    via the two-stage majority vote."""
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
    run_one: Callable[[dict, dict, float], Any],  # (record, spend_state, max_spend_usd) -> raw-run dataclass
    checkpoint_path: Union[str, Path],
    max_spend_usd: float,
    k: int = 3,
) -> list[ConsensusQuestionRecord]:
    """Cell-agnostic: run_one is a partially-applied C1/C2 run function, this
    module only owns k-repeat/checkpoint/vote machinery. Checkpoints per
    (collision_id, repeat_index) so a partial sweep resumes mid-question.
    """
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

            from offline.pilot import SpendCapExceeded  # local import, avoids a load-time cycle

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
    """exact_match=None has two causes here -- unverified annotation vs.
    consensus abstention -- and conflating them into one count would
    misreport abstention as an annotation backlog, so split explicitly."""
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
