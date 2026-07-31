"""Chain-of-Verification orchestrator. CoVe-off's `PassthroughVerifier` is
the identity, used by C1. `CoVeVerifier` below is the real implementation,
used by C2+ (`enable_cove: true`).

Four steps, three of them LLM calls (question extraction, independent
execution, prose comparison — numeric comparison is explicitly excluded,
it's deterministic code):

  1. Draft `D` — already built by the caller (generator's `answer_text`,
     or a draft assembler's templated combination under NPCE).
  2. Claim extraction (6a) — LLM extracts every atomic claim from `D`.
     Verification questions are then generated deterministically from
     those claims by template (`_verification_question` below) — the
     question-generation step itself is deterministic, only claim
     extraction is LLM.
  3. Independent execution (6b) — LLM answers each verification question
     against the paired FFF evidence only, with `D` structurally absent
     from the call. `execute_independent_verification` takes no `draft`
     parameter at all — this is a signature-level guarantee, not a prompt
     instruction, so a future change can't reintroduce the draft "for
     context" without visibly changing this signature and failing
     `test_cove_independence`. Do not add one.
  4. Revise (6c) — numeric claims compared by `numeric_match` (deterministic,
     never an LLM judgment on whether two numbers are equal); non-numeric
     claims compared by an LLM prose-comparison call.

`CoVeVerifier.verify()` returns the revised answer text unchanged (matching,
conflicting, and unsupported claims are flagged, not rewritten — the
abstention gate, not this orchestrator, decides whether a flagged claim
sinks the whole answer) plus the full per-claim trace.
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
    """CoVe-off: the identity. The draft reaches citation binding
    unmodified, with an empty verification trace — there's nothing to
    report because nothing was checked, not because everything passed."""

    def verify(self, draft: str, evidence: Sequence[FFF], ctx: RequestContext) -> VerifiedAnswer:
        return VerifiedAnswer(answer_text=draft, verification_trace=())


def _verification_question(entity: str, metric: str, period_label: str) -> str:
    """Verification questions are generated deterministically from these
    fields by template — not part of the claim-extraction LLM call."""
    return f"What was {entity} {metric} for {period_label}?"


def extract_claims(draft: str, llm_client: Optional[LLMClient] = None) -> list[dict]:
    """Claim extraction. Returns raw claim dicts (schema-validated by the
    adapter) — kept as dicts rather than `ClaimExtractionItem` instances so
    callers can feed them straight into `numeric_match`
    (`online/verification/numeric_comparator.py`), which takes plain dicts."""
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
    """Independent verification. No `draft` parameter — do not add one.
    Independence is enforced by this function's signature, asserted by
    `test_cove_independence` (tests/test_acceptance.py), not by a prompt
    instruction a model could ignore."""
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
    """Prose comparison, invoked only when `is_numeric == false`. Returns
    the verdict string directly ("match" | "conflict" | "unsupported") —
    numeric claims never reach here, they go to `numeric_match`
    (deterministic, see `test_numeric_comparison_deterministic`)."""
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
    """Real CoVe implementation, used by C2+. Three independent
    `LLMClient`s — one per sub-call — because each has a different output
    schema even though they share a single model choice
    (`cove_verification` in config; see `llm/routing.py:build_llm_client`'s
    `schema_key` parameter for how one config entry yields three clients).
    """

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
