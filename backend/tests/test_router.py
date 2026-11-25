"""online/routing/router.py: the straight, money-split and step-by-step
roads, and the 6-piece cap. Steps 4.1 and 4.6. Paper-split, fix-the-question
and try-again live in their own modules and are not tested here."""
from __future__ import annotations

from online.routing.router import MAX_PIECES, cap_pieces, choose_road, describe_routing


def test_a_plain_one_fact_question_takes_the_straight_road():
    decision = choose_road("What was 3M's accounts payable for FY2018?")

    assert decision.road == "straight"
    assert decision.signal is None


def test_revenue_and_net_income_splits():
    decision = choose_road("What were 3M's revenue and net income for FY2018?")

    assert decision.road == "split_money"
    assert decision.signal == "and"


def test_compare_a_and_b_splits():
    decision = choose_road("Compare 3M and Adobe's net income for FY2018.")

    assert decision.road == "split_money"


def test_both_triggers_a_split():
    decision = choose_road("Give me both accounts payable and accounts receivable.")

    assert decision.road == "split_money"


def test_each_triggers_a_split():
    decision = choose_road("What is the net income for each of 3M's segments?")

    assert decision.road == "split_money"


def test_versus_triggers_a_split():
    decision = choose_road("3M versus Adobe net income for FY2018.")

    assert decision.road == "split_money"


def test_vs_abbreviation_triggers_a_split():
    decision = choose_road("3M vs Adobe net income.")

    assert decision.road == "split_money"


def test_and_inside_a_word_does_not_falsely_trigger_a_split():
    """"and" as a whole word only -- a word merely containing "and" (like a
    company name) must never trip a false split."""
    decision = choose_road("What was Sandwich Co's net income for FY2018?")

    assert decision.road == "straight"


def test_each_inside_a_word_does_not_falsely_trigger_a_split():
    decision = choose_road("What are the total reaches for the campaign?")

    assert decision.road == "straight"


def test_the_original_question_is_never_altered():
    question = "What were 3M's revenue and net income for FY2018?"

    decision = choose_road(question)

    assert decision.question == question


def test_the_reason_names_the_triggering_word():
    """"Compare" is matched first (it appears earliest in the sentence) --
    a valid, informative signal on its own, even though "and" is also
    present later in the same question."""
    decision = choose_road("Compare 3M and Adobe.")

    assert "compare" in decision.reason


# --- step 4.6: the step-by-step road ---


def test_a_question_needing_another_piece_first_takes_the_step_by_step_road():
    decision = choose_road("What was the operating margin of the company with the highest revenue in 2022?")

    assert decision.road == "step_by_step"
    assert decision.signal is not None


def test_whichever_phrasing_also_triggers_step_by_step():
    decision = choose_road("What was the net income of whichever segment had the largest loss in 2022?")

    assert decision.road == "step_by_step"


def test_split_signal_wins_over_step_signal_when_both_are_present():
    """Decision 11's own diagram order: split is checked before
    step-dependency -- a question with both signals still splits."""
    decision = choose_road("Compare 3M and the company with the highest revenue in 2022.")

    assert decision.road == "split_money"


def test_a_superlative_with_a_named_company_does_not_falsely_trigger_step_by_step():
    """The company is already named -- nothing here needs a first step to
    find out which one, so this must stay straight."""
    decision = choose_road("What was 3M's highest quarterly revenue in 2022?")

    assert decision.road == "straight"


def test_step_by_step_reason_names_the_triggering_phrase():
    decision = choose_road("What was the revenue of the company with the highest market cap in 2022?")

    assert "company with" in decision.reason


def test_describe_routing_for_the_step_by_step_road():
    decision = choose_road("What was the operating margin of the company with the highest revenue in 2022?")

    log = describe_routing(decision, piece_count=1, capped=False)

    assert log["road"] == "step_by_step"
    assert "step by step" in log["summary"].lower()


# --- the 6-piece cap ---


def test_six_pieces_or_fewer_are_never_capped():
    pieces = list(range(6))

    result, capped = cap_pieces(pieces)

    assert result == pieces
    assert capped is False


def test_seven_pieces_are_capped_at_six():
    pieces = list(range(7))

    result, capped = cap_pieces(pieces)

    assert len(result) == 6
    assert result == pieces[:6]
    assert capped is True


def test_zero_pieces_is_not_capped():
    result, capped = cap_pieces([])

    assert result == []
    assert capped is False


def test_a_custom_cap_is_honoured():
    result, capped = cap_pieces([1, 2, 3, 4], max_pieces=2)

    assert result == [1, 2]
    assert capped is True


def test_max_pieces_constant_is_six():
    assert MAX_PIECES == 6


# --- the plain-words log, "How it happened"'s data ---


def test_describe_routing_for_the_straight_road():
    decision = choose_road("What was 3M's net income for FY2018?")

    log = describe_routing(decision, piece_count=1, capped=False)

    assert log["road"] == "straight"
    assert log["capped"] is False
    assert "no split" in log["summary"] or "straight" in log["summary"].lower()


def test_describe_routing_for_a_split_road():
    decision = choose_road("3M's revenue and net income for FY2018?")

    log = describe_routing(decision, piece_count=2, capped=False)

    assert log["road"] == "split_money"
    assert log["piece_count"] == 2
    assert "2 pieces" in log["summary"]


def test_describe_routing_notes_when_capped():
    decision = choose_road("3M's revenue and net income and margin and eps and assets and cash and debt for FY2018?")

    log = describe_routing(decision, piece_count=6, capped=True)

    assert log["capped"] is True
    assert "Capped" in log["summary"] or "capped" in log["summary"].lower()


def test_describe_routing_always_includes_the_original_question():
    question = "What was 3M's net income for FY2018?"
    decision = choose_road(question)

    log = describe_routing(decision, piece_count=1, capped=False)

    assert log["question"] == question
