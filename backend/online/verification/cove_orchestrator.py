"""Chain-of-Verification orchestrator. `PassthroughVerifier` is the CoVe-off
identity (C1); `CoVeVerifier` is the real thing (C2+).

Four steps, three are LLM calls (extraction, independent execution, prose
comparison); numeric comparison stays deterministic code:

  1. Draft D — built by the caller already (generator or assembler output).
  2. Claim extraction (6a) — LLM pulls atomic claims out of D. Verification
     questions are then templated deterministically from those claims.
  3. Independent execution (6b) — LLM answers each verification question
     against FFF evidence only. `execute_independent_verification` takes
     no `draft` param at all, on purpose — a signature-level guarantee
     backing `test_cove_independence`. Do not add one.
  4. Revise (6c) — numeric claims go through `numeric_match` (code, never
     an LLM judging equality); non-numeric claims get an LLM prose compare.

`verify()` returns the answer text unchanged; flagged claims aren't
rewritten here, the abstention gate decides what to do with them.
"""
from __future__ import annotations

from typing import Optional, Sequence

from contracts.models import FFF, RequestContext, VerificationClaimResult, VerifiedAnswer
from llm.client import LLMClient
from online.verification.numeric_comparator import numeric_match

SCHEMA_VERSION = "1.0"

CLAIM_EXTRACTION_SYSTEM_PROMPT = """\
You extract every atomic factual claim from a draft financial answer.

An atomic claim is a single assertion of one value, for one entity, one metric,
one period. Split compound sentences into separate claims.

Extract claims exactly as the draft states them. Do not evaluate, correct, or
comment on whether they are right -- that happens later and by a different process.
"""

INDEPENDENT_VERIFICATION_SYSTEM_PROMPT = """\
You answer a factual question using only the evidence provided.

You have no other context. Do not speculate about what the expected answer might be.

If the evidence supports an answer, give it exactly as the evidence states it,
including the period and scale that evidence actually covers. If the evidence
does not support an answer, set supported to false. "Not supported" is a
correct and useful answer -- it is how unsupported claims get caught.
"""

PROSE_COMPARISON_SYSTEM_PROMPT = """\
You compare two non-numeric statements and judge whether they assert the same thing.

Return "match" only if they assert the same fact. Return "conflict" if they assert
different facts. Return "unsupported" if the verification result asserts nothing.

Differences of wording, order, or verbosity are not conflicts. Differences of
substance are.
"""


class PassthroughVerifier:
    """CoVe-off identity. Empty trace means nothing was checked, not that
    everything passed."""

    def verify(self, draft: str, evidence: Sequence[FFF], ctx: RequestContext) -> VerifiedAnswer:
        return VerifiedAnswer(answer_text=draft, verification_trace=())


def _verification_question(entity: str, metric: str, period_label: str) -> str:
    """Templated, not part of the claim-extraction LLM call."""
    return f"What was {entity} {metric} for {period_label}?"


def extract_claims(draft: str, llm_client: Optional[LLMClient] = None) -> list[dict]:
    """Returns raw claim dicts, not `ClaimExtractionItem` instances, so
    callers can feed them straight into `numeric_match`."""
    client = llm_client if llm_client is not None else LLMClient()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": CLAIM_EXTRACTION_SYSTEM_PROMPT,
        "draft": draft,
    }
    response = client.complete(payload)
    return response.get("claims", [])


def execute_independent_verification(
    question: str,
    evidence: Sequence[FFF],
    llm_client: Optional[LLMClient] = None,
) -> dict:
    """No `draft` parameter, do not add one. Independence is enforced by
    the signature (`test_cove_independence`), not a prompt instruction."""
    client = llm_client if llm_client is not None else LLMClient()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": INDEPENDENT_VERIFICATION_SYSTEM_PROMPT,
        "verification_question": question,
        "evidence": [
            {
                "fff_id": f.fff_id,
                "subject": f.subject,
                "metric": f.metric,
                "period_label": f.period_label,
                "value_text": f.value_text,
            }
            for f in evidence
        ],
    }
    return client.complete(payload)


def compare_prose_claim(
    claim_text: str,
    verification_answer_text: str,
    llm_client: Optional[LLMClient] = None,
) -> str:
    """Only for `is_numeric == false` claims. Numeric claims never reach
    here, they go to `numeric_match` instead."""
    client = llm_client if llm_client is not None else LLMClient()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": PROSE_COMPARISON_SYSTEM_PROMPT,
        "claim_text": claim_text,
        "verification_answer_text": verification_answer_text,
    }
    response = client.complete(payload)
    return response.get("verdict", "unsupported")


class CoVeVerifier:
    """Real CoVe implementation, used by C2+. Three separate LLMClients
    because each sub-call has a different output schema."""

    def __init__(
        self,
        claim_extraction_client: LLMClient,
        independent_verification_client: LLMClient,
        prose_comparison_client: LLMClient,
    ) -> None:
        self._claim_extraction_client = claim_extraction_client
        self._independent_verification_client = independent_verification_client
        self._prose_comparison_client = prose_comparison_client

    def verify(self, draft: str, evidence: Sequence[FFF], ctx: RequestContext) -> VerifiedAnswer:
        raw_claims = extract_claims(draft, self._claim_extraction_client)

        trace: list[VerificationClaimResult] = []
        for i, claim in enumerate(raw_claims):
            claim_id = claim.get("claim_id") or f"c_{i + 1}"
            claim_text = claim.get("claim_text", "")
            is_numeric = bool(claim.get("is_numeric", False))

            question = _verification_question(
                claim.get("entity", ""), claim.get("metric", ""), claim.get("period_label", "")
            )
            verification = execute_independent_verification(
                question, evidence, self._independent_verification_client
            )

            supporting_fff_ids = tuple(verification.get("supporting_fff_ids", []))

            if not verification.get("supported", False):
                verdict = "unsupported"
            elif is_numeric:
                verdict = "match" if numeric_match(claim, verification) else "conflict"
            else:
                verdict = compare_prose_claim(
                    claim_text, verification.get("answer_value", "") or "", self._prose_comparison_client
                )

            trace.append(
                VerificationClaimResult(
                    claim_id=claim_id,
                    claim_text=claim_text,
                    is_numeric=is_numeric,
                    verdict=verdict,  # type: ignore[arg-type]
                    supporting_fff_ids=supporting_fff_ids,
                )
            )

        return VerifiedAnswer(answer_text=draft, verification_trace=tuple(trace))
