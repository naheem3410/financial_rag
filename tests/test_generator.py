"""Generator tests. A scripted fake client exercises the real guard logic
without a real provider wired in.
"""
from __future__ import annotations

from contracts.models import FFF, SourceRef
from online.output.generator import AnswerValue, generate_answer


def _make_fff(fff_id: str, metric: str, value_text: str, period_label: str = "FY2018") -> FFF:
    return FFF(
        fff_id=fff_id,
        proposition_id=f"p_{fff_id}",
        subject="3M Company",
        metric=metric,
        period_label=period_label,
        value_text=value_text,
        provenance_tier="retrieved",
        source_ref=SourceRef(filing_type="10-K", filing_date="2018-12-31", page=57),
    )


class _FakeLLMClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def test_answers_a_single_metric_question():
    fact = _make_fff("f_1", "net_sales", "$32,765 million")
    client = _FakeLLMClient(
        [{
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "net_sales", "answer_value": "$32,765 million", "answer_unit": "millions"}],
            "answer_text": "3M's net sales were $32,765 million.", "used_fff_ids": ["f_1"],
        }]
    )

    result = generate_answer("What were 3M's net sales?", [fact], client)

    assert result.answerable is True
    assert result.answers == [AnswerValue(metric="net_sales", value="$32,765 million", unit="millions")]
    assert result.used_fff_ids == ["f_1"]


def test_answers_two_metrics_from_two_facts():
    fact_a = _make_fff("f_1", "accounts_receivable", "$4,532 million")
    fact_b = _make_fff("f_2", "total_current_assets", "$14,688 million")
    client = _FakeLLMClient(
        [{
            "reasoning": "...", "answerable": True,
            "answers": [
                {"metric": "accounts_receivable", "answer_value": "$4,532 million", "answer_unit": "millions"},
                {"metric": "total_current_assets", "answer_value": "$14,688 million", "answer_unit": "millions"},
            ],
            "answer_text": "Accounts receivable: $4,532 million. Total current assets: $14,688 million.",
            "used_fff_ids": ["f_1", "f_2"],
        }]
    )

    result = generate_answer("What were 3M's accounts receivable and total current assets?", [fact_a, fact_b], client)

    assert len(result.answers) == 2
    values = {a.metric: a.value for a in result.answers}
    assert values == {"accounts_receivable": "$4,532 million", "total_current_assets": "$14,688 million"}


def test_drops_only_the_fabricated_answer_keeping_the_genuine_one():
    fact = _make_fff("f_1", "net_sales", "$32,765 million")
    client = _FakeLLMClient(
        [
            {
                "reasoning": "...", "answerable": True,
                "answers": [
                    {"metric": "net_sales", "answer_value": "$32,765 million", "answer_unit": "millions"},
                    {"metric": "fabricated_metric", "answer_value": "$99,999 million", "answer_unit": "millions"},
                ],
                "answer_text": "...", "used_fff_ids": ["f_1"],
            },
        ]
    )

    result = generate_answer("q", [fact], client)

    assert result.answerable is True
    assert [a.metric for a in result.answers] == ["net_sales"]


def test_answerable_false_is_accepted_immediately_no_retry():
    client = _FakeLLMClient(
        [{"reasoning": "...", "answerable": False, "answers": [], "answer_text": "Not stated in the facts.", "used_fff_ids": []}]
    )

    result = generate_answer("q", [_make_fff("f_1", "net_sales", "$1 million")], client)

    assert result.answerable is False
    assert len(client.calls) == 1


def test_all_answers_fabricated_retries_then_falls_back_to_abstain():
    fact = _make_fff("f_1", "net_sales", "$32,765 million")
    client = _FakeLLMClient(
        [
            {"reasoning": "...", "answerable": True, "answers": [{"metric": "net_sales", "answer_value": "$1 million", "answer_unit": "millions"}], "answer_text": "...", "used_fff_ids": ["f_1"]},
            {"reasoning": "...", "answerable": True, "answers": [{"metric": "net_sales", "answer_value": "$2 million", "answer_unit": "millions"}], "answer_text": "...", "used_fff_ids": ["f_1"]},
        ]
    )

    result = generate_answer("q", [fact], client, max_attempts=2)

    assert result.answerable is False
    assert len(client.calls) == 2


def test_used_fff_ids_referencing_an_unsupplied_fact_are_filtered_out():
    fact = _make_fff("f_1", "net_sales", "$32,765 million")
    client = _FakeLLMClient(
        [{
            "reasoning": "...", "answerable": True,
            "answers": [{"metric": "net_sales", "answer_value": "$32,765 million", "answer_unit": "millions"}],
            "answer_text": "...", "used_fff_ids": ["f_1", "f_999"],
        }]
    )

    result = generate_answer("q", [fact], client)

    assert result.used_fff_ids == ["f_1"]


def test_no_facts_short_circuits_without_calling_the_llm():
    client = _FakeLLMClient([])

    result = generate_answer("q", [], client)

    assert result.answerable is False
    assert len(client.calls) == 0
