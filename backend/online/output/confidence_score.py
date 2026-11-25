"""Confidence score: the fraction of CoVe's extracted claims that matched
the evidence. Pure function of the verification trace, zero LLM calls --
same input the abstention gate already looks at, just reported as a number
instead of collapsed to a yes/no.
"""
from __future__ import annotations

from typing import Sequence

from contracts.models import VerificationClaimResult


def compute(trace: Sequence[VerificationClaimResult]) -> float:
    if not trace:
        return 0.0
    matched = sum(1 for claim in trace if claim.verdict == "match")
    return round(matched / len(trace), 4)
