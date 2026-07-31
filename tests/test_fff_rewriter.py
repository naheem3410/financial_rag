"""FFF rewriter tests. A scripted fake client exercises the real guard
logic without a real provider wired in.
"""
from __future__ import annotations

from contracts.models import Confidence, Proposition, PropositionMetadata
from online.curation.fff_rewriter import rewrite_to_ffts


def _make_proposition(proposition_id: str, text: str, company: str = "3M Company", page: int = 57) -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company=company, filing_type="10-K", filing_date="2018-12-31",
            section="", page=page, source_uri="3M_2018_10K",
        ),
    )


class _FakeLLMClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def test_accepts_a_genuine_fact_and_sets_subject_from_proposition_metadata():
    prop = _make_proposition("p_1", "3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    client = _FakeLLMClient(
        [{"reasoning": "...", "facts": [
            {"proposition_id": "p_1", "subject": "the company", "metric": "net_sales", "period_label": "FY2018", "value_text": "$32,765 million"},
        ]}]
    )

    result = rewrite_to_ffts("What were 3M's net sales?", [prop], client)

    assert len(result) == 1
    fff = result[0]
    assert fff.subject == "3M Company"  # overridden from metadata, NOT the model's "the company"
    assert fff.value_text == "$32,765 million"
    assert fff.proposition_id == "p_1"
    assert fff.source_ref.page == 57
    assert fff.source_ref.filing_type == "10-K"


def test_subject_override_replaces_proposition_metadata_company():
    """C3/C4 (NPCE-on): subject must become the sub-query's placeholder
    token, not the real company name -- passing subject_override overrides
    the C1/C2 default (source_prop.metadata.company) entirely."""
    prop = _make_proposition("p_1", "3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    client = _FakeLLMClient(
        [{"reasoning": "...", "facts": [
            {"proposition_id": "p_1", "subject": "3M Company", "metric": "net_sales", "period_label": "FY2018", "value_text": "$32,765 million"},
        ]}]
    )

    result = rewrite_to_ffts("What was <ENT_1>'s net sales?", [prop], client, subject_override="<ENT_1>")

    assert len(result) == 1
    assert result[0].subject == "<ENT_1>"  # not "3M Company" -- the real name never leaks through


def test_rejects_a_fact_with_a_fabricated_number():
    prop = _make_proposition("p_1", "3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    client = _FakeLLMClient(
        [
            {"reasoning": "...", "facts": [
                {"proposition_id": "p_1", "subject": "3M", "metric": "net_sales", "period_label": "FY2018", "value_text": "$99,999 million"},
            ]},
            {"reasoning": "...", "facts": []},
        ]
    )

    result = rewrite_to_ffts("What were 3M's net sales?", [prop], client, max_attempts=2)

    assert result == []
    assert len(client.calls) == 2  # rejected fact -> retried once


def test_rejects_a_fact_with_a_mislabeled_period():
    """The fbc_metric_pri_0061 mechanism (online/curation/numeric_guard.py):
    the source genuinely states December 31, 2022, but the model relabels
    the fact as FY2023 -- matching what a question might ask for rather
    than what the source says. Must be rejected exactly like a fabricated
    number, not silently trusted."""
    prop = _make_proposition("p_1", "3M Company had accounts payable of $3,183 million as of December 31, 2022.")
    client = _FakeLLMClient(
        [
            {"reasoning": "...", "facts": [
                {"proposition_id": "p_1", "subject": "3M", "metric": "accounts_payable", "period_label": "FY2023", "value_text": "$3,183 million"},
            ]},
            {"reasoning": "...", "facts": []},
        ]
    )

    result = rewrite_to_ffts("What was 3M's accounts payable for FY2023?", [prop], client, max_attempts=2)

    assert result == []
    assert len(client.calls) == 2  # rejected fact -> retried once


def test_rejects_a_fact_referencing_an_unsupplied_proposition_id():
    prop = _make_proposition("p_1", "3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    client = _FakeLLMClient(
        [
            {"reasoning": "...", "facts": [
                {"proposition_id": "p_999", "subject": "3M", "metric": "net_sales", "period_label": "FY2018", "value_text": "$32,765 million"},
            ]},
            {"reasoning": "...", "facts": []},
        ]
    )

    result = rewrite_to_ffts("What were 3M's net sales?", [prop], client, max_attempts=2)

    assert result == []


def test_a_correct_empty_response_is_not_retried():
    """The model correctly reporting 'no facts concern this entity' is a
    valid answer, not a failure to retry against."""
    prop = _make_proposition("p_1", "Adobe Systems Incorporated had gross profit of $1 million.", company="Adobe Systems Incorporated")
    client = _FakeLLMClient([{"reasoning": "...", "facts": []}])

    result = rewrite_to_ffts("What were 3M's net sales?", [prop], client, max_attempts=3)

    assert result == []
    assert len(client.calls) == 1  # not retried


def test_no_propositions_short_circuits_without_calling_the_llm():
    client = _FakeLLMClient([])

    result = rewrite_to_ffts("What were 3M's net sales?", [], client)

    assert result == []
    assert len(client.calls) == 0


def test_multiple_facts_from_different_propositions_each_get_their_own_subject_and_source_ref():
    prop_a = _make_proposition("p_1", "3M Company had net sales of $32,765 million in the year ended December 31, 2018.", company="3M Company", page=55)
    prop_b = _make_proposition("p_2", "3M Company had operating income of $7,207 million in the year ended December 31, 2018.", company="3M Company", page=56)
    client = _FakeLLMClient(
        [{"reasoning": "...", "facts": [
            {"proposition_id": "p_1", "subject": "3M", "metric": "net_sales", "period_label": "FY2018", "value_text": "$32,765 million"},
            {"proposition_id": "p_2", "subject": "3M", "metric": "operating_income", "period_label": "FY2018", "value_text": "$7,207 million"},
        ]}]
    )

    result = rewrite_to_ffts("What were 3M's net sales and operating income?", [prop_a, prop_b], client)

    assert len(result) == 2
    by_metric = {f.metric: f for f in result}
    assert by_metric["net_sales"].source_ref.page == 55
    assert by_metric["operating_income"].source_ref.page == 56


def test_a_fact_from_a_different_company_in_the_same_batch_gets_that_companys_subject():
    """C1 has no Metadata Filter constraint (documented no-op under
    passthrough), so a reranked batch can legitimately span more than one
    company -- subject must reflect each fact's OWN linked proposition, not
    leak another company's name across the batch."""
    prop_3m = _make_proposition("p_1", "3M Company had net sales of $32,765 million.", company="3M Company")
    prop_adobe = _make_proposition("p_2", "Adobe Systems Incorporated had gross profit of $1,000 million.", company="Adobe Systems Incorporated")
    client = _FakeLLMClient(
        [{"reasoning": "...", "facts": [
            {"proposition_id": "p_1", "subject": "wrong", "metric": "net_sales", "period_label": "FY2018", "value_text": "$32,765 million"},
            {"proposition_id": "p_2", "subject": "also wrong", "metric": "gross_profit", "period_label": "FY2018", "value_text": "$1,000 million"},
        ]}]
    )

    result = rewrite_to_ffts("q", [prop_3m, prop_adobe], client)

    by_metric = {f.metric: f for f in result}
    assert by_metric["net_sales"].subject == "3M Company"
    assert by_metric["gross_profit"].subject == "Adobe Systems Incorporated"
