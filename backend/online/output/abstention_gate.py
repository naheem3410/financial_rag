"""Abstention gate: a rule over verification results, never an LLM. Any
material claim unsupported -> abstain.

Sees the same VerificationClaimResult shape whether CoVe ran (C2+, real
trace) or not (C1, caller synthesizes one) -- doesn't need to know which.
Every non-"match" verdict counts as material; no ranking yet since C1/C2
only ever have one claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from contracts.models import VerificationClaimResult


@dataclass(frozen=True)
class AbstentionDecision:
    abstained: bool
    message: str


def decide(trace: Sequence[VerificationClaimResult]) -> AbstentionDecision:
    unsupported = [claim for claim in trace if claim.verdict != "match"]
    if not unsupported:
        return AbstentionDecision(abstained=False, message="all claims supported by the available filings.")

    claim_texts = "; ".join(claim.claim_text for claim in unsupported)
    return AbstentionDecision(
        abstained=True,
        message=f"not supported by the available filings: {claim_texts}",
    )
