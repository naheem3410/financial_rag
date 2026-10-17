"""offline/documents/policy_answer.py: the direct-answer + verbatim-excerpt
+ pinpoint-citation format, and the honest out-of-scope message."""
from __future__ import annotations

from contracts.models import PolicyFact
from offline.documents.policy_answer import assemble_policy_answer, render_out_of_scope, render_policy_answer


def _fact(**overrides) -> PolicyFact:
    defaults = dict(
        fact_id="f_1", document_id="doc_1",
        text="Minors may open an account. However, a guardian must co-sign.",
        section_path=("Accounts", "Eligibility", "Minors"), section_number="4.2.1",
        consequent="Minors may open an account.", exceptions=("a guardian must co-sign.",),
        page=6, source_uri="policy.pdf",
    )
    defaults.update(overrides)
    return PolicyFact(**defaults)


def test_direct_answer_uses_the_consequent_not_the_whole_block():
    answer = assemble_policy_answer(_fact(), document_title="Account Handbook")

    assert answer.direct_answer == "Minors may open an account."


def test_excerpt_is_the_full_verbatim_rule_block_not_just_the_consequent():
    answer = assemble_policy_answer(_fact(), document_title="Account Handbook")

    assert answer.excerpt == "Minors may open an account. However, a guardian must co-sign."


def test_when_no_consequent_was_extracted_the_whole_text_is_the_direct_answer():
    fact = _fact(consequent=None)

    answer = assemble_policy_answer(fact, document_title="Account Handbook")

    assert answer.direct_answer == fact.text


def test_citation_is_pinpoint_document_page_section_and_heading_path():
    answer = assemble_policy_answer(_fact(), document_title="Account Handbook")

    assert answer.citation == "Account Handbook, page 7, Section 4.2.1 (Accounts -> Eligibility -> Minors)"


def test_page_in_the_citation_is_one_indexed_for_a_person_to_read():
    """PolicyFact.page is zero-indexed internally; the citation a person
    reads must not be off by one."""
    answer = assemble_policy_answer(_fact(page=0), document_title="Account Handbook")

    assert answer.citation.startswith("Account Handbook, page 1")


def test_citation_without_a_section_number_still_shows_the_heading_path():
    fact = _fact(section_number=None)

    answer = assemble_policy_answer(fact, document_title="Account Handbook")

    assert "Section" not in answer.citation
    assert "Accounts -> Eligibility -> Minors" in answer.citation


def test_citation_without_any_heading_path_still_names_the_document_and_page():
    fact = _fact(section_number=None, section_path=())

    answer = assemble_policy_answer(fact, document_title="Account Handbook")

    assert answer.citation == "Account Handbook, page 7"


def test_render_policy_answer_has_the_two_part_shape():
    answer = assemble_policy_answer(_fact(), document_title="Account Handbook")

    rendered = render_policy_answer(answer)

    assert rendered.startswith("Minors may open an account.\n\n")
    assert '"Minors may open an account. However, a guardian must co-sign."' in rendered
    assert rendered.endswith("-- Account Handbook, page 7, Section 4.2.1 (Accounts -> Eligibility -> Minors)")


def test_out_of_scope_names_what_is_covered_instead_of_guessing():
    message = render_out_of_scope(
        "international student accounts", "Account Handbook",
        covered_sections=[("Accounts", "Eligibility", "Minors"), ("Accounts", "Eligibility", "Seniors")],
    )

    assert message == (
        "Account Handbook does not explicitly cover international student accounts. "
        "It covers: Accounts -> Eligibility -> Minors; Accounts -> Eligibility -> Seniors."
    )


def test_out_of_scope_with_nothing_covered_still_gives_a_plain_message():
    message = render_out_of_scope("anything", "Empty Handbook", covered_sections=[])

    assert message == "Empty Handbook does not explicitly cover anything."


def test_out_of_scope_never_infers_a_likely_answer():
    """The exact "bad AI behaviour" from the design discussion must not
    appear anywhere in the message."""
    message = render_out_of_scope("international students", "Account Handbook", covered_sections=[])

    assert "usually" not in message.lower()
    assert "typically" not in message.lower()
