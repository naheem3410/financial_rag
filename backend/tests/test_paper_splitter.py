"""online/routing/paper_splitter.py: paper split (step 4.3), both halves --
the free rule-based check, tried first always, and the model fallback
(call site: paper_split_fallback, owner's yes given 2026-09-26) used only
when the rules find nothing."""
from __future__ import annotations

from online.routing.paper_splitter import (
    detect_plain_comparison,
    find_grounded_phrases,
    find_look_alike_groups_llm_fallback,
    split_by_look_alikes,
    split_paper_question,
)


class _SequencedClient:
    """Same fake used across the pipeline tests: returns each queued
    response in order, no real model call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def test_the_plans_own_example_splits_into_two():
    question = "What is the loan limit for savings vs current accounts?"

    phrases = detect_plain_comparison(question)

    assert phrases == ["savings accounts", "current accounts"]


def test_versus_spelled_out_is_recognized():
    phrases = detect_plain_comparison("What is the loan limit for savings versus current accounts?")

    assert phrases == ["savings accounts", "current accounts"]


def test_or_is_recognized_as_a_comparison():
    phrases = detect_plain_comparison("Can I use a savings or current account for this?")

    assert phrases == ["savings account", "current account"]


def test_a_question_that_must_not_split_stays_one():
    """No "X vs Y <shared word>" shape here at all -- correctly finds
    nothing to split."""
    phrases = detect_plain_comparison("What is the minimum balance for a savings account?")

    assert phrases == []


def test_a_plain_yes_no_question_stays_one():
    phrases = detect_plain_comparison("Can I open a savings account online?")

    assert phrases == []


def test_split_by_look_alikes_swaps_in_one_phrase_at_a_time():
    question = "What is the loan limit for savings vs current accounts?"
    phrases = detect_plain_comparison(question)

    result = split_by_look_alikes(question, phrases)

    assert result.pieces == (
        "What is the loan limit for savings accounts?",
        "What is the loan limit for current accounts?",
    )
    assert result.capped is False


def test_split_by_look_alikes_caps_at_six():
    question = "Which of these applies: a, b, c, d, e, f, g?"
    seven_phrases = ["a", "b", "c", "d", "e", "f", "g"]

    result = split_by_look_alikes(question, seven_phrases)

    assert len(result.pieces) == 6
    assert result.capped is True


def test_split_by_look_alikes_with_a_custom_cap():
    result = split_by_look_alikes("q?", ["a", "b", "c"], max_pieces=2)

    assert len(result.pieces) == 2
    assert result.capped is True


def test_split_by_look_alikes_records_which_phrases_were_used():
    question = "What is the loan limit for savings vs current accounts?"
    phrases = detect_plain_comparison(question)

    result = split_by_look_alikes(question, phrases)

    assert result.phrases == ("savings accounts", "current accounts")


# --- find_grounded_phrases: the safety check any model output must pass ---


def test_grounded_phrases_are_kept():
    question = "What is the loan limit for savings vs current accounts?"

    result = find_grounded_phrases(question, ["savings", "current accounts"])

    assert result == ["savings", "current accounts"]


def test_a_phrase_the_model_invents_that_is_not_in_the_question_is_rejected():
    question = "What is the loan limit for savings vs current accounts?"

    result = find_grounded_phrases(question, ["savings", "fixed deposit accounts"])

    assert result == ["savings"]


def test_grounding_is_case_insensitive():
    question = "What is the loan limit for Savings vs Current accounts?"

    result = find_grounded_phrases(question, ["savings", "current"])

    assert result == ["savings", "current"]


def test_grounding_matches_whole_words_only():
    """"loan" must not match inside "loaner" or similar -- a phrase is only
    grounded if it appears as its own word(s)."""
    question = "What is the loaner policy for staff?"

    result = find_grounded_phrases(question, ["loan"])

    assert result == []


def test_no_candidate_phrases_grounded_returns_an_empty_list():
    result = find_grounded_phrases("What is the loan limit?", ["fixed deposit", "mortgage"])

    assert result == []


def test_nigerian_phrasing_the_plain_comparison_detector_still_works():
    """A locally common phrasing style -- the rule only depends on the
    "X vs Y <shared word>" shape, not on any particular dialect otherwise."""
    phrases = detect_plain_comparison("Please what is the account opening requirement for salary vs corporate accounts?")

    assert phrases == ["salary accounts", "corporate accounts"]


# --- find_look_alike_groups_llm_fallback: model half, owner's yes given 2026-09-26 ---


def test_llm_fallback_accepts_phrases_grounded_in_the_question():
    question = "What documents do I need for a student account compared to a joint account?"
    client = _SequencedClient([{"reasoning": "...", "phrases": ["student account", "joint account"]}])

    result = find_look_alike_groups_llm_fallback(question, client)

    assert result == ["student account", "joint account"]


def test_llm_fallback_drops_a_phrase_the_model_invents():
    """The model names something not actually in the question -- rejected,
    same as every other fallback's "classify/extract, never invent" check."""
    question = "What documents do I need for a student account compared to a joint account?"
    client = _SequencedClient([{"reasoning": "...", "phrases": ["student account", "diaspora account"]}])

    result = find_look_alike_groups_llm_fallback(question, client)

    assert result == ["student account"]


def test_llm_fallback_empty_phrases_is_a_correct_answer():
    question = "What is the minimum balance for a savings account?"
    client = _SequencedClient([{"reasoning": "...", "phrases": []}])

    result = find_look_alike_groups_llm_fallback(question, client)

    assert result == []


def test_llm_fallback_schema_invalid_response_is_dropped_not_guessed():
    question = "What documents do I need for a student account compared to a joint account?"
    client = _SequencedClient([{"phrases": "not-a-list"}])  # wrong type, missing required "reasoning"

    result = find_look_alike_groups_llm_fallback(question, client)

    assert result == []


def test_llm_fallback_sends_the_question_text():
    client = _SequencedClient([{"reasoning": "...", "phrases": []}])

    find_look_alike_groups_llm_fallback("the exact question text", client)

    assert client.calls[0]["question"] == "the exact question text"


# --- split_paper_question: the composed entry point ---


def test_split_paper_question_uses_the_free_rule_without_any_client():
    question = "What is the loan limit for savings vs current accounts?"

    result = split_paper_question(question)

    assert result is not None
    assert result.phrases == ("savings accounts", "current accounts")


def test_split_paper_question_returns_none_with_nothing_to_split_and_no_client():
    result = split_paper_question("What is the minimum balance for a savings account?")

    assert result is None


def test_split_paper_question_never_calls_the_model_when_rules_already_succeeded():
    question = "What is the loan limit for savings vs current accounts?"
    client = _SequencedClient([{"reasoning": "should not be used", "phrases": ["a", "b"]}])

    result = split_paper_question(question, client)

    assert result.phrases == ("savings accounts", "current accounts")
    assert client.calls == []


def test_split_paper_question_falls_back_to_the_model_when_rules_find_nothing():
    question = "What documents do I need for a student account compared to a joint account?"
    client = _SequencedClient([{"reasoning": "...", "phrases": ["student account", "joint account"]}])

    result = split_paper_question(question, client)

    assert result is not None
    assert result.phrases == ("student account", "joint account")
    assert len(client.calls) == 1


def test_split_paper_question_returns_none_when_the_model_also_finds_nothing():
    question = "What documents do I need to open an account?"
    client = _SequencedClient([{"reasoning": "...", "phrases": []}])

    result = split_paper_question(question, client)

    assert result is None


def test_split_paper_question_returns_none_when_the_model_finds_only_one_grounded_phrase():
    """A single phrase is nothing to split into two pieces -- same "< 2 is
    no split" rule as the free rule-based path."""
    question = "What documents do I need for a student account compared to something else?"
    client = _SequencedClient([{"reasoning": "...", "phrases": ["student account", "invented other account"]}])

    result = split_paper_question(question, client)

    assert result is None
