"""Fake clients exercise the real verdict/trace logic without a live provider.
Independence and CoVe-off passthrough are covered in test_acceptance.py, not here.
"""
from __future__ import annotations

from contracts.models import FFF, RequestContext, SourceRef
from online.verification.cove_orchestrator import CoVeVerifier


def _make_fff(fff_id: str, metric: str, value_text: str, period_label: str = "FY2023") -> FFF:
    return FFF(
        fff_id=fff_id,
        proposition_id=f"p_{fff_id}",
        subject="3M Company",
        metric=metric,
        period_label=period_label,
        value_text=value_text,
        provenance_tier="retrieved",
        source_ref=SourceRef(filing_type="10-K", filing_date="2023-12-31", page=57),
    )


class _FakeLLMClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def _ctx() -> RequestContext:
    return RequestContext(request_id="r_1", config_hash="cfg_abc")


def test_matching_numeric_claim_is_kept_zero_llm_involvement_in_the_compare():
    """Agreement on value/unit/period/scale verdicts "match" via numeric_match (DET) alone,
    no extra LLM call for the compare step."""
    extraction_client = _FakeLLMClient(
        [{
            "reasoning": "...",
            "claims": [{
                "claim_id": "c_1", "claim_text": "3M revenue for FY2023 was $32.2 billion.",
                "entity": "3M Company", "metric": "revenue", "period_label": "FY2023",
                "asserted_value": "32.2", "asserted_unit": "USD", "scale": "billions", "is_numeric": True,
            }],
        }]
    )
    verification_client = _FakeLLMClient(
        [{
            "reasoning": "...", "supported": True,
            "answer_value": "32.2", "answer_unit": "USD", "period_label": "FY2023", "scale": "billions",
            "supporting_fff_ids": ["f_1"],
        }]
    )
    prose_client = _FakeLLMClient([])  # never invoked - claim is numeric

    verifier = CoVeVerifier(extraction_client, verification_client, prose_client)
    result = verifier.verify(
        "3M revenue for FY2023 was $32.2 billion.",
        evidence=[_make_fff("f_1", "revenue", "$32.2 billion")],
        ctx=_ctx(),
    )

    assert result.answer_text == "3M revenue for FY2023 was $32.2 billion."
    assert len(result.verification_trace) == 1
    assert result.verification_trace[0].verdict == "match"
    assert result.verification_trace[0].supporting_fff_ids == ("f_1",)
    assert len(prose_client.calls) == 0


def test_temporal_collision_wrong_period_evidence_yields_conflict_not_match():
    """Draft claims FY2023 but the evidence verification actually answers from is FY2022,
    a real period mismatch. numeric_match must catch it via period_label, not the value (which matches)."""
    extraction_client = _FakeLLMClient(
        [{
            "reasoning": "...",
            "claims": [{
                "claim_id": "c_1", "claim_text": "3M revenue for FY2023 was $30.0 billion.",
                "entity": "3M Company", "metric": "revenue", "period_label": "FY2023",
                "asserted_value": "30.0", "asserted_unit": "USD", "scale": "billions", "is_numeric": True,
            }],
        }]
    )
    verification_client = _FakeLLMClient(
        [{
            "reasoning": "the only evidence available is for FY2022, not FY2023",
            "supported": True,
            "answer_value": "30.0", "answer_unit": "USD", "period_label": "FY2022", "scale": "billions",
            "supporting_fff_ids": ["f_1"],
        }]
    )
    prose_client = _FakeLLMClient([])

    verifier = CoVeVerifier(extraction_client, verification_client, prose_client)
    result = verifier.verify(
        "3M revenue for FY2023 was $30.0 billion.",
        evidence=[_make_fff("f_1", "revenue", "$30.0 billion", period_label="FY2022")],
        ctx=_ctx(),
    )

    assert result.verification_trace[0].verdict == "conflict"


def test_unsupported_verification_yields_unsupported_verdict():
    extraction_client = _FakeLLMClient(
        [{
            "reasoning": "...",
            "claims": [{
                "claim_id": "c_1", "claim_text": "3M revenue for FY2025 was $40 billion.",
                "entity": "3M Company", "metric": "revenue", "period_label": "FY2025",
                "asserted_value": "40", "asserted_unit": "USD", "scale": "billions", "is_numeric": True,
            }],
        }]
    )
    verification_client = _FakeLLMClient(
        [{"reasoning": "no evidence covers FY2025", "supported": False, "supporting_fff_ids": []}]
    )
    prose_client = _FakeLLMClient([])

    verifier = CoVeVerifier(extraction_client, verification_client, prose_client)
    result = verifier.verify(
        "3M revenue for FY2025 was $40 billion.",
        evidence=[_make_fff("f_1", "revenue", "$32.2 billion")],
        ctx=_ctx(),
    )

    assert result.verification_trace[0].verdict == "unsupported"
    assert len(prose_client.calls) == 0


def test_non_numeric_claim_routes_to_prose_comparison_not_numeric_match():
    extraction_client = _FakeLLMClient(
        [{
            "reasoning": "...",
            "claims": [{
                "claim_id": "c_1",
                "claim_text": "The increase was driven by organic growth across all business segments.",
                "entity": "3M Company", "metric": "growth_driver", "period_label": "FY2023",
                "is_numeric": False,
            }],
        }]
    )
    verification_client = _FakeLLMClient(
        [{
            "reasoning": "...", "supported": True,
            "answer_value": "organic growth across all segments", "answer_unit": None,
            "period_label": "FY2023", "scale": None, "supporting_fff_ids": ["f_1"],
        }]
    )
    prose_client = _FakeLLMClient([{"reasoning": "same substance, different wording", "verdict": "match"}])

    verifier = CoVeVerifier(extraction_client, verification_client, prose_client)
    result = verifier.verify(
        "The increase was driven by organic growth across all business segments.",
        evidence=[_make_fff("f_1", "growth_driver", "organic growth across all segments")],
        ctx=_ctx(),
    )

    assert len(prose_client.calls) == 1
    assert result.verification_trace[0].verdict == "match"
    assert result.verification_trace[0].is_numeric is False


def test_multiple_claims_produce_one_trace_entry_each():
    extraction_client = _FakeLLMClient(
        [{
            "reasoning": "...",
            "claims": [
                {
                    "claim_id": "c_1", "claim_text": "revenue was $32.2 billion",
                    "entity": "3M Company", "metric": "revenue", "period_label": "FY2023",
                    "asserted_value": "32.2", "asserted_unit": "USD", "scale": "billions", "is_numeric": True,
                },
                {
                    "claim_id": "c_2", "claim_text": "net income was $5.4 billion",
                    "entity": "3M Company", "metric": "net_income", "period_label": "FY2023",
                    "asserted_value": "5.4", "asserted_unit": "USD", "scale": "billions", "is_numeric": True,
                },
            ],
        }]
    )
    verification_client = _FakeLLMClient(
        [
            {
                "reasoning": "...", "supported": True, "answer_value": "32.2", "answer_unit": "USD",
                "period_label": "FY2023", "scale": "billions", "supporting_fff_ids": ["f_1"],
            },
            {
                "reasoning": "...", "supported": True, "answer_value": "9.9", "answer_unit": "USD",
                "period_label": "FY2023", "scale": "billions", "supporting_fff_ids": ["f_2"],
            },
        ]
    )
    prose_client = _FakeLLMClient([])

    verifier = CoVeVerifier(extraction_client, verification_client, prose_client)
    result = verifier.verify(
        "revenue was $32.2 billion; net income was $5.4 billion",
        evidence=[
            _make_fff("f_1", "revenue", "$32.2 billion"),
            _make_fff("f_2", "net_income", "$5.4 billion"),
        ],
        ctx=_ctx(),
    )

    assert len(result.verification_trace) == 2
    assert result.verification_trace[0].verdict == "match"
    assert result.verification_trace[1].verdict == "conflict"  # 5.4 asserted vs 9.9 independently verified


def test_no_claims_extracted_yields_empty_trace():
    extraction_client = _FakeLLMClient([{"reasoning": "no factual claims in this draft", "claims": []}])
    verification_client = _FakeLLMClient([])
    prose_client = _FakeLLMClient([])

    verifier = CoVeVerifier(extraction_client, verification_client, prose_client)
    result = verifier.verify("Not supported by the available filings.", evidence=[], ctx=_ctx())

    assert result.verification_trace == ()
    assert len(verification_client.calls) == 0
