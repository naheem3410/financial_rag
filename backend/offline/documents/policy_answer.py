"""Formats one policy fact into an answer: a direct statement, then the
exact source text, with a pinpoint citation -- never a bare document name.
Deterministic, no model call: the "direct answer" is the fact's own
consequent (or the whole rule block, if the rule-based split found no
connector to separate one). Real paraphrase/synthesis of that into a more
natural sentence is future work, the same "needs its own yes" call-site
rule as everywhere else new AI use has been added this session.

Decision (design discussion, 2026-09-26): never let a policy answer imply
certainty the source doesn't support. Asking about something the uploaded
document doesn't cover gets told plainly what is missing, never a guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from contracts.models import PolicyFact


@dataclass(frozen=True)
class PolicyAnswer:
    direct_answer: str
    excerpt: str
    document_title: str
    page: int
    section_number: str | None
    section_path: tuple[str, ...]

    @property
    def citation(self) -> str:
        location = f"{self.document_title}, page {self.page + 1}"
        if self.section_number:
            location += f", Section {self.section_number}"
        if self.section_path:
            location += f" ({' -> '.join(self.section_path)})"
        return location


def assemble_policy_answer(fact: PolicyFact, document_title: str) -> PolicyAnswer:
    direct_answer = fact.consequent or fact.text
    return PolicyAnswer(
        direct_answer=direct_answer, excerpt=fact.text, document_title=document_title,
        page=fact.page, section_number=fact.section_number, section_path=fact.section_path,
    )


def render_policy_answer(answer: PolicyAnswer) -> str:
    """The two-part shape: a direct answer, then the verbatim excerpt with
    its pinpoint citation -- so a person can check the exact source before
    acting on the summary alone."""
    return f'{answer.direct_answer}\n\n"{answer.excerpt}"\n-- {answer.citation}'


def render_out_of_scope(question_topic: str, document_title: str, covered_sections: Sequence[tuple[str, ...]]) -> str:
    """Never infer past what the document says. States what the document
    does cover instead of guessing at what it doesn't."""
    if covered_sections:
        covered = "; ".join(" -> ".join(path) for path in covered_sections)
        return f"{document_title} does not explicitly cover {question_topic}. It covers: {covered}."
    return f"{document_title} does not explicitly cover {question_topic}."
