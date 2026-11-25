from contracts.models import VerificationClaimResult
from online.output.confidence_score import compute


def _claim(verdict: str, claim_id: str = "c_1") -> VerificationClaimResult:
    return VerificationClaimResult(claim_id=claim_id, claim_text="x", is_numeric=True, verdict=verdict)


def test_all_claims_matched_is_full_confidence(llm_call_counter):
    trace = [_claim("match", "c_1"), _claim("match", "c_2")]
    assert compute(trace) == 1.0
    assert llm_call_counter.count == 0


def test_mixed_verdicts_give_the_matched_fraction(llm_call_counter):
    trace = [_claim("match", "c_1"), _claim("conflict", "c_2"), _claim("unsupported", "c_3")]
    assert compute(trace) == round(1 / 3, 4)
    assert llm_call_counter.count == 0


def test_no_claims_at_all_is_zero_confidence(llm_call_counter):
    assert compute([]) == 0.0
    assert llm_call_counter.count == 0


def test_no_matches_is_zero_confidence(llm_call_counter):
    trace = [_claim("unsupported", "c_1"), _claim("conflict", "c_2")]
    assert compute(trace) == 0.0
    assert llm_call_counter.count == 0
