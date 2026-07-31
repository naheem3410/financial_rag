"""Draft assembler tests."""
from __future__ import annotations

from contracts.models import Period, SubQuery
from online.generation.assembler import SubAnswer, assemble_draft
from online.output.generator import AnswerValue, GeneratedAnswer


def _sub_query(entity: str, metric: str, period_label: str = "FY2022") -> SubQuery:
    return SubQuery(
        sub_query_id=f"sq_{entity}_{metric}", entity=entity, metric=metric,
        period=Period(fiscal_year=2022), period_label=period_label,
    )


def test_two_answerable_sub_queries_both_reported():
    sub_answers = [
        SubAnswer(
            sub_query=_sub_query("<ENT_1>", "net_income"),
            generated=GeneratedAnswer(answerable=True, answers=[AnswerValue(metric="net_income", value="$5,777 million", unit="USD millions")]),
        ),
        SubAnswer(
            sub_query=_sub_query("<ENT_1>", "total_revenue"),
            generated=GeneratedAnswer(answerable=True, answers=[AnswerValue(metric="total_revenue", value="$34,229 million", unit="USD millions")]),
        ),
    ]

    draft = assemble_draft(sub_answers)

    assert "<ENT_1> net income (FY2022): $5,777 million USD millions" in draft
    assert "<ENT_1> total revenue (FY2022): $34,229 million USD millions" in draft
    assert draft.count("\n") == 1  # exactly two lines


def test_unanswerable_sub_query_reported_as_not_provided_not_omitted():
    """This is the exact case NPCE is meant to fix (capacity/partial-drop)
    -- if it still happens under C3, the draft must say so honestly
    rather than silently dropping the line."""
    sub_answers = [
        SubAnswer(
            sub_query=_sub_query("<ENT_1>", "net_income"),
            generated=GeneratedAnswer(answerable=True, answers=[AnswerValue(metric="net_income", value="$5,777 million", unit="USD millions")]),
        ),
        SubAnswer(
            sub_query=_sub_query("<ENT_1>", "total_revenue"),
            generated=GeneratedAnswer(answerable=False, answers=[]),
        ),
    ]

    draft = assemble_draft(sub_answers)

    assert "<ENT_1> total revenue (FY2022): not provided." in draft


def test_no_period_label_omits_the_parenthetical():
    sub_answers = [
        SubAnswer(
            sub_query=_sub_query("<ENT_1>", "operating_margin", period_label=""),
            generated=GeneratedAnswer(answerable=True, answers=[AnswerValue(metric="operating_margin", value="18.2 percent", unit="")]),
        ),
    ]

    draft = assemble_draft(sub_answers)

    assert draft == "<ENT_1> operating margin: 18.2 percent"


def test_empty_input_yields_empty_draft():
    assert assemble_draft([]) == ""


def test_introduces_no_facts_beyond_what_generator_reported():
    """The assembler must never compute, sum, or infer anything -- every
    line is a verbatim restatement of one AnswerValue already produced by
    the real generator call."""
    av = AnswerValue(metric="net_income", value="$5,777 million", unit="USD millions")
    sub_answers = [SubAnswer(sub_query=_sub_query("<ENT_1>", "net_income"), generated=GeneratedAnswer(answerable=True, answers=[av]))]

    draft = assemble_draft(sub_answers)

    assert av.value in draft
    assert av.unit in draft
