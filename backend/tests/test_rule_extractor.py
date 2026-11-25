"""offline/documents/rule_extractor.py: rule-based consequent/exception
split for policy rule blocks. Antecedent extraction is not attempted here
(see the module docstring)."""
from __future__ import annotations

from offline.documents.policy_chunker import RuleBlockDraft
from offline.documents.rule_extractor import (
    extract_antecedent_llm_fallback,
    rule_block_to_fact,
    split_consequent_and_exceptions,
)


class _SequencedClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def test_no_connector_the_whole_text_is_the_consequent():
    text = "Applicants must be 18 years or older."

    consequent, exceptions = split_consequent_and_exceptions(text)

    assert consequent == text
    assert exceptions == ()


def test_the_users_worked_example_three_sentences_split_correctly():
    """The exact scenario from the design discussion: a base rule, a
    "however" amendment, and a "this does not apply to" exception."""
    text = (
        "Minors may open an account. "
        "However, they must be accompanied by a guardian if under 16. "
        "This does not apply to emancipated minors in the state of NY."
    )

    consequent, exceptions = split_consequent_and_exceptions(text)

    assert consequent == "Minors may open an account."
    assert exceptions == (
        "they must be accompanied by a guardian if under 16.",
        "emancipated minors in the state of NY.",
    )


def test_except_connector_is_recognized():
    text = "All accounts require a co-signer. Except accounts opened by active-duty military personnel."

    consequent, exceptions = split_consequent_and_exceptions(text)

    assert consequent == "All accounts require a co-signer."
    assert exceptions == ("accounts opened by active-duty military personnel.",)


def test_unless_connector_is_recognized():
    text = "The fee is waived. Unless the account balance falls below $500."

    _, exceptions = split_consequent_and_exceptions(text)

    assert exceptions == ("the account balance falls below $500.",)


def test_a_connector_used_mid_sentence_not_at_a_sentence_start_is_not_split():
    """A known, honest limit: "unless" appearing inside the first sentence
    (no sentence boundary before it) stays part of the consequent."""
    text = "Minors may open an account unless they are wards of the state."

    consequent, exceptions = split_consequent_and_exceptions(text)

    assert consequent == text
    assert exceptions == ()


def test_the_first_sentence_is_never_treated_as_an_exception_even_if_it_starts_with_a_connector():
    text = "Unless otherwise stated, all fees are non-refundable."

    consequent, exceptions = split_consequent_and_exceptions(text)

    assert consequent == text
    assert exceptions == ()


def test_multiple_exceptions_are_each_kept_separate():
    text = "All deposits are insured. Except cryptocurrency holdings. Unless otherwise required by law."

    _, exceptions = split_consequent_and_exceptions(text)

    assert exceptions == ("cryptocurrency holdings.", "otherwise required by law.")


def test_empty_text_yields_an_empty_consequent_and_no_exceptions():
    assert split_consequent_and_exceptions("") == ("", ())


def test_rule_block_to_fact_builds_a_complete_policy_fact():
    block = RuleBlockDraft(
        text="Minors may open an account. However, a guardian must co-sign.",
        section_path=("Accounts", "Eligibility", "Minors"), section_number="4.2.1", page=3,
    )

    fact = rule_block_to_fact(block, fact_id="f_1", document_id="doc_1", source_uri="policy.pdf")

    assert fact.fact_id == "f_1"
    assert fact.document_id == "doc_1"
    assert fact.text == block.text  # verbatim, untouched
    assert fact.section_path == ("Accounts", "Eligibility", "Minors")
    assert fact.section_number == "4.2.1"
    assert fact.consequent == "Minors may open an account."
    assert fact.exceptions == ("a guardian must co-sign.",)
    assert fact.antecedent is None  # not attempted by the rule-based pass
    assert fact.page == 3
    assert fact.source_uri == "policy.pdf"


# --- antecedent extraction: model fallback, owner's yes given 2026-09-26 ---


def test_llm_fallback_accepts_an_antecedent_grounded_in_the_text():
    text = "Applicants under the age of 18 years may open a custodial account."
    client = _SequencedClient([{"reasoning": "...", "antecedent": "Applicants under the age of 18 years"}])

    result = extract_antecedent_llm_fallback(text, client)

    assert result == "Applicants under the age of 18 years"


def test_llm_fallback_drops_an_antecedent_not_grounded_in_the_text():
    """The model invents a condition the block never stated -- rejected,
    not trusted, same as the metric labeler's closed-lexicon check."""
    text = "Applicants under the age of 18 years may open a custodial account."
    client = _SequencedClient([{"reasoning": "...", "antecedent": "employees hired before the merger"}])

    result = extract_antecedent_llm_fallback(text, client)

    assert result is None


def test_llm_fallback_null_antecedent_is_a_correct_unconditional_answer():
    text = "All customers receive a debit card upon account opening."
    client = _SequencedClient([{"reasoning": "...", "antecedent": None}])

    result = extract_antecedent_llm_fallback(text, client)

    assert result is None


def test_llm_fallback_schema_invalid_response_is_dropped_not_guessed():
    text = "Applicants under 18 may open an account."
    client = _SequencedClient([{"antecedent": 12345}])  # wrong type, and missing required "reasoning"

    result = extract_antecedent_llm_fallback(text, client)

    assert result is None


def test_llm_fallback_sends_the_rule_block_text():
    client = _SequencedClient([{"reasoning": "...", "antecedent": None}])

    extract_antecedent_llm_fallback("the exact block text", client)

    assert client.calls[0]["rule_block"] == "the exact block text"


def test_rule_block_to_fact_without_a_client_leaves_antecedent_none_exactly_like_before():
    block = RuleBlockDraft(text="Applicants under 18 may open an account.", section_path=("Minors",), section_number="4.2.1", page=0)

    fact = rule_block_to_fact(block, fact_id="f_1", document_id="doc_1", source_uri="p.pdf")

    assert fact.antecedent is None


def test_rule_block_to_fact_with_a_client_fills_in_a_grounded_antecedent():
    block = RuleBlockDraft(text="Applicants under 18 may open an account.", section_path=("Minors",), section_number="4.2.1", page=0)
    client = _SequencedClient([{"reasoning": "...", "antecedent": "Applicants under 18"}])

    fact = rule_block_to_fact(block, fact_id="f_1", document_id="doc_1", source_uri="p.pdf", llm_client=client)

    assert fact.antecedent == "Applicants under 18"


def test_grounding_check_rejects_a_phrase_with_little_word_overlap_with_the_source():
    text = "Applicants under the age of 18 years may open a custodial account."
    client = _SequencedClient([{"reasoning": "...", "antecedent": "employees who joined after the corporate merger"}])

    result = extract_antecedent_llm_fallback(text, client)

    assert result is None  # essentially no shared words with the source block


def test_grounding_check_accepts_a_phrase_with_strong_word_overlap_with_the_source():
    text = "Applicants under the age of 18 years may open a custodial account."
    client = _SequencedClient([{"reasoning": "...", "antecedent": "applicants under the age of 18 years"}])

    result = extract_antecedent_llm_fallback(text, client)

    assert result == "applicants under the age of 18 years"
