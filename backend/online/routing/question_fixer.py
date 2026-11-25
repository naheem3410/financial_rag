"""Fix the question (step 4.4, decision 11): a question with a small typo
in a company name gets fixed automatically; a question that leaves out
something important -- like "What was it in 2019?" with no company named
anywhere -- gets an honest "which company do you mean?" instead of a guess.

No model call here, on purpose: both checks are rule-based against the
same company list the query pipeline already carries (the placeholder
registry, step 2.1) -- a spelling fix and a missing-reference check don't
need a model to get right, and a plain rule can't invent an answer the way
a model might.

Honest limitation: fuzzy typo-matching can occasionally "fix" a real but
unfamiliar company name into a similarly-spelled known one. This only
happens when the word is close enough (see `fix_typos`'s cutoff) to an
existing name and isn't an exact match to any known name already -- the
same trade-off any spell-checker makes.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Literal, Optional, Sequence

Outcome = Literal["unchanged", "fixed", "needs_clarification"]

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
_PRONOUN_RE = re.compile(r"\b(it|its|they|their|them)\b", re.IGNORECASE)

_MIN_TYPO_WORD_LENGTH = 3
_TYPO_CUTOFF = 0.8


@dataclass(frozen=True)
class QuestionFix:
    outcome: Outcome
    question: str  # what to actually use going forward -- the fixed text if outcome == "fixed", the original otherwise
    original_question: str
    fixes: tuple[tuple[str, str], ...]  # (typo, correction) pairs, for the log
    clarification_message: Optional[str] = None


def _mentions_a_known_company(question: str, known_names: Sequence[str]) -> bool:
    lowered = question.lower()
    return any(re.search(rf"\b{re.escape(name.lower())}\b", lowered) for name in known_names if name)


def fix_typos(question: str, known_names: Sequence[str], cutoff: float = _TYPO_CUTOFF) -> tuple[str, list[tuple[str, str]]]:
    """Corrects a capitalized word that closely -- but not exactly --
    matches a known company name. The sentence-initial word is never
    touched, since ordinary capitalization there is not a proper-noun
    signal. Rebuilt from the original text with a single left-to-right
    pass, so replacements never fight over shifting positions."""
    lowered_known = {name.lower(): name for name in known_names if name}
    fixes: list[tuple[str, str]] = []
    parts: list[str] = []
    cursor = 0
    for i, match in enumerate(_WORD_RE.finditer(question)):
        word = match.group(0)
        replacement = word
        if i != 0 and word[0].isupper():
            lowered_word = word.lower()
            has_possessive = lowered_word.endswith("'s")
            base = lowered_word[:-2] if has_possessive else lowered_word
            if len(base) >= _MIN_TYPO_WORD_LENGTH and base not in lowered_known:
                candidates = difflib.get_close_matches(base, lowered_known.keys(), n=1, cutoff=cutoff)
                if candidates:
                    correct = lowered_known[candidates[0]]
                    if correct.lower() != base:
                        replacement = f"{correct}'s" if has_possessive else correct
                        fixes.append((word, replacement))
        parts.append(question[cursor:match.start()])
        parts.append(replacement)
        cursor = match.end()
    parts.append(question[cursor:])
    return "".join(parts), fixes


def fix_question(question: str, known_names: Sequence[str]) -> QuestionFix:
    """The one function the router should call. Tries a typo fix first;
    only asks for clarification if nothing was fixed, the question uses an
    unresolved pronoun ("it"/"they"/...), and no known company is named
    anywhere in it -- the plan's own "What was it in 2019?" case. A clear
    question with nothing to fix and no missing reference is returned
    untouched."""
    fixed_text, fixes = fix_typos(question, known_names)
    if fixes:
        return QuestionFix(outcome="fixed", question=fixed_text, original_question=question, fixes=tuple(fixes))
    if _PRONOUN_RE.search(question) and not _mentions_a_known_company(question, known_names):
        return QuestionFix(
            outcome="needs_clarification",
            question=question,
            original_question=question,
            fixes=(),
            clarification_message="Which company is this question about?",
        )
    return QuestionFix(outcome="unchanged", question=question, original_question=question, fixes=())
