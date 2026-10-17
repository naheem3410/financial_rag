"""online/routing/question_fixer.py: fix the question (step 4.4, decision
11). Both checks -- typo fix, missing-reference clarification -- are
rule-based against the known company list; no model call in this step."""
from __future__ import annotations

from online.routing.question_fixer import fix_question, fix_typos

KNOWN_NAMES = ["Apple", "Apple Inc", "3M", "3M Company", "Costco"]


# --- fix_typos: the standalone correction check ---


def test_a_close_typo_is_corrected():
    fixed, fixes = fix_typos("What was Aple's revenue in 2019?", KNOWN_NAMES)

    assert fixed == "What was Apple's revenue in 2019?"
    assert fixes == [("Aple's", "Apple's")]


def test_a_correctly_spelled_name_is_left_alone():
    fixed, fixes = fix_typos("What was Apple's revenue in 2019?", KNOWN_NAMES)

    assert fixed == "What was Apple's revenue in 2019?"
    assert fixes == []


def test_the_sentence_initial_word_is_never_touched():
    """"What" is capitalized only because it starts the sentence -- never a
    proper-noun signal, must never be "corrected" into a company name."""
    fixed, fixes = fix_typos("What was the revenue in 2019?", ["What-ever Corp"])

    assert fixed == "What was the revenue in 2019?"
    assert fixes == []


def test_an_unrelated_word_is_not_fuzzy_matched():
    fixed, fixes = fix_typos("What was the Revenue in 2019?", KNOWN_NAMES)

    assert fixed == "What was the Revenue in 2019?"
    assert fixes == []


def test_lowercase_words_are_never_considered_typos():
    fixed, fixes = fix_typos("what was aple's revenue in 2019?", KNOWN_NAMES)

    assert fixed == "what was aple's revenue in 2019?"
    assert fixes == []


def test_two_typos_in_one_question_are_both_fixed():
    fixed, fixes = fix_typos("Compare Aple and Costcoo revenue in 2019.", KNOWN_NAMES)

    assert fixed == "Compare Apple and Costco revenue in 2019."
    assert len(fixes) == 2


def test_a_short_word_is_not_fuzzy_matched_even_if_close():
    """Below the minimum length, fuzzy matching is skipped entirely --
    short words are too likely to coincidentally resemble a real name."""
    fixed, fixes = fix_typos("Am I eligible?", ["Am Corp"])

    assert fixed == "Am I eligible?"
    assert fixes == []


def test_a_non_possessive_typo_is_corrected_without_adding_a_clitic():
    fixed, fixes = fix_typos("What did Costcoo report in 2019?", KNOWN_NAMES)

    assert fixed == "What did Costco report in 2019?"
    assert fixes == [("Costcoo", "Costco")]


def test_no_known_names_means_nothing_is_ever_fixed():
    fixed, fixes = fix_typos("What was Aple's revenue in 2019?", [])

    assert fixed == "What was Aple's revenue in 2019?"
    assert fixes == []


# --- fix_question: the composed entry point ---


def test_the_plans_own_missing_company_example_asks():
    result = fix_question("What was it in 2019?", KNOWN_NAMES)

    assert result.outcome == "needs_clarification"
    assert result.clarification_message is not None
    assert result.question == "What was it in 2019?"


def test_a_typo_in_a_company_name_is_fixed():
    result = fix_question("What was Aple's revenue in 2019?", KNOWN_NAMES)

    assert result.outcome == "fixed"
    assert result.question == "What was Apple's revenue in 2019?"
    assert result.fixes == (("Aple's", "Apple's"),)
    assert result.original_question == "What was Aple's revenue in 2019?"


def test_a_clear_question_is_untouched():
    result = fix_question("What was Apple's revenue in 2019?", KNOWN_NAMES)

    assert result.outcome == "unchanged"
    assert result.question == "What was Apple's revenue in 2019?"
    assert result.fixes == ()
    assert result.clarification_message is None


def test_a_pronoun_with_the_company_already_named_is_not_ambiguous():
    """"it" here plainly refers to the company already named in the same
    question -- a real company is present, so this does not ask."""
    result = fix_question("What was Apple's revenue and how did it compare to last year?", KNOWN_NAMES)

    assert result.outcome == "unchanged"


def test_a_question_with_no_pronoun_and_no_company_is_not_asked_about():
    """Only the plan's specific ambiguous-pronoun case triggers a
    clarification -- a generic question with nothing to point at is left
    alone, not flagged just for lacking a company name."""
    result = fix_question("What is a good savings rate?", KNOWN_NAMES)

    assert result.outcome == "unchanged"


def test_an_empty_known_company_list_still_asks_on_a_bare_pronoun():
    result = fix_question("What was it in 2019?", [])

    assert result.outcome == "needs_clarification"


def test_fixing_a_typo_takes_priority_over_asking():
    """A pronoun elsewhere in the question doesn't block the typo fix --
    fixing what can be fixed always comes first."""
    result = fix_question("What was Aple's revenue and how did it compare to last year?", KNOWN_NAMES)

    assert result.outcome == "fixed"
    assert "Apple's" in result.question
