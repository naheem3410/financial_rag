"""online/routing/how_it_happened.py: step 4.7, "How it happened". Pure
assembly over an already-finished C4AnswerResult -- no pipeline run here,
just constructs results directly with the fields this step added."""
from __future__ import annotations

from online.pipeline_c4 import C4AnswerResult
from online.routing.how_it_happened import describe_how_it_happened

_COMMON = dict(
    answerable=True, answers=[], citations=[], used_fff_ids=[],
    query_splitter_model="m", fff_rewriter_model="m", generator_model="m", cove_verification_model="m",
    verification_trace=[],
)


def _result(**overrides) -> C4AnswerResult:
    fields = dict(
        question="What was 3M's net income for FY2022?",
        abstained=False, abstention_message="", answer_text="3M's net income was $5,777 million.",
        sub_query_count=1, fff_count=1,
        **_COMMON,
    )
    fields.update(overrides)
    return C4AnswerResult(**fields)


def test_straight_road_says_answered_straight():
    result = _result(routing_road="straight", routing_reason="no split or step-dependency signal found", pieces=())

    how = describe_how_it_happened(result)

    assert how.road == "straight"
    assert "answered straight" in how.summary.lower()
    assert how.checked_against_original_question is True


def test_split_road_shows_the_pieces():
    result = _result(
        routing_road="split_money", routing_reason='the question contains "and"',
        pieces=("What was 3M's net income for FY2022?", "What was 3M's total revenue for FY2022?"),
        sub_query_count=2,
    )

    how = describe_how_it_happened(result)

    assert how.pieces == (
        "What was 3M's net income for FY2022?",
        "What was 3M's total revenue for FY2022?",
    )
    assert "2 pieces" in how.summary
    assert how.checked_against_original_question is True


def test_capped_split_says_so():
    result = _result(
        routing_road="split_money", routing_reason='the question contains "and"',
        pieces=tuple(f"piece {i}" for i in range(6)), routing_capped=True, sub_query_count=6,
    )

    how = describe_how_it_happened(result)

    assert "capped" in how.summary.lower()


def test_retry_shows_the_other_names():
    result = _result(retry_attempted=True, retry_names_tried=("3M Company",))

    how = describe_how_it_happened(result)

    assert how.retry_attempted is True
    assert how.retry_names_tried == ("3M Company",)


def test_no_retry_shows_no_names():
    result = _result()

    how = describe_how_it_happened(result)

    assert how.retry_attempted is False
    assert how.retry_names_tried == ()


def test_abstain_still_shows_the_section():
    result = _result(
        abstained=True, abstention_message="Not enough evidence to answer confidently.",
        answer_text="", routing_road="straight", pieces=(),
    )

    how = describe_how_it_happened(result)

    assert how.road == "straight"
    assert how.summary  # still populated, not empty, even though the answer abstained


def test_needs_clarification_has_its_own_summary():
    result = _result(
        question_fix_outcome="needs_clarification", abstained=True,
        abstention_message="Which company is this question about?", answer_text="",
        sub_query_count=0, fff_count=0, pieces=(),
    )

    how = describe_how_it_happened(result)

    assert "guessing" in how.summary.lower()


def test_a_fixed_typo_is_mentioned_in_the_summary():
    result = _result(question_fix_outcome="fixed", question_fixes=(("Aple's", "Apple's"),), pieces=())

    how = describe_how_it_happened(result)

    assert "typo" in how.summary.lower()


def test_step_by_step_shows_the_steps_and_an_honest_original_question_flag():
    """Step 4.6's own known gap: each step's own check is against that
    step's re-phrased question, not the true original -- this must be
    reported honestly, not glossed over as "checked against the original"."""
    step_plan = (
        {"step_id": 1, "question": "Which company had the highest revenue in 2022?", "answer_text": "3M had the highest revenue.", "answerable": True},
        {"step_id": 2, "question": "Given that 3M had the highest revenue, what was its net income in 2022?", "answer_text": "3M's net income was $5,777 million.", "answerable": True},
    )
    result = _result(
        routing_road="step_by_step",
        routing_reason='the question needs one piece\'s answer before it can ask the next ("the company with the highest")',
        step_plan=step_plan, sub_query_count=2, pieces=(step_plan[0]["question"], step_plan[1]["question"]),
    )

    how = describe_how_it_happened(result)

    assert how.steps == step_plan
    assert "step by step" in how.summary.lower()
    assert "2 step" in how.summary
    assert how.checked_against_original_question is False


def test_a_rejected_step_plan_falls_back_to_describing_the_real_piece_count():
    """The router still classifies the question as step_by_step even when
    the plan itself was rejected and the ordinary path answered it instead
    (step 4.6's own safe default) -- the summary must describe what
    actually happened, not the road label alone."""
    result = _result(
        routing_road="step_by_step", routing_reason="the question needs one piece's answer...",
        step_plan=(), pieces=(), sub_query_count=1,
    )

    how = describe_how_it_happened(result)

    assert how.steps == ()
    assert "answered straight" in how.summary.lower()
    assert how.checked_against_original_question is True


def test_several_versions_note_is_none_when_nothing_was_found():
    result = _result()

    how = describe_how_it_happened(result)

    assert how.several_versions_note is None


def test_several_versions_note_reports_a_real_basis_collision():
    """Built 2026-09-27, decision 11's own test row: "Two near-identical
    results | Both answered and labelled". Once a collision actually
    fires, the note and the summary both say so honestly."""
    result = _result(
        near_identical_detected=True, near_identical_detail="basis",
        answer_text="3M's net income was $5,777 million (originally reported) / $5,820 million (restated).",
    )

    how = describe_how_it_happened(result)

    assert how.several_versions_note is not None
    assert "restated" in how.several_versions_note.lower()
    assert "labelled" in how.summary.lower()


def test_question_understood_and_original_question_are_both_reported():
    result = _result(question="What was Aple's net income for FY2022?", question_understood="What was Apple's net income for FY2022?", pieces=())

    how = describe_how_it_happened(result)

    assert how.original_question == "What was Aple's net income for FY2022?"
    assert how.question_understood == "What was Apple's net income for FY2022?"
