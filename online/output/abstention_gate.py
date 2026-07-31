"""Abstention gate — negative rejection. Rules over verification results,
never an LLM: whether to abstain is a rule (any material claim unsupported
-> abstain), not a judgment call.

Takes a verification trace (`VerificationClaimResult`) — with CoVe on
(C2+), this is CoVe's real per-claim trace; with CoVe off (C1), the caller
synthesizes a trace representing what CoVe would otherwise have caught
(fires when no supporting FFF exists — i.e. the generator had nothing to
answer from, or explicitly returned `answerable: false`). Either way this
module only ever sees the same `VerificationClaimResult` shape and applies
the same rule — the abstention gate doesn't need to know whether CoVe ran.

An empty trace means nothing was flagged (every claim matched, or CoVe is
off and the caller had a genuine answer) — not abstained. Any claim whose
`verdict` is not `"match"` is treated as material — C1/C2 here have
exactly one claim per answer, so there's no non-central claim to
distinguish; a real multi-claim materiality ranking is deferred to
whichever cell first needs one.
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
