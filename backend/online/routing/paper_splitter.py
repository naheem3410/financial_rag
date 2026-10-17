"""Paper split (non-finance NPCE): splits a question that compares two or
more look-alike things ("loan limit for savings vs current accounts") into
one plain question per thing, so a document card filter (product/audience/
version) can narrow each piece correctly. Step 4.3.

Two parts, on purpose:
- The rule-based part handles the plain, explicit case ("X vs Y <shared
  word>") with no model at all -- real, tested, free, tried first always.
- `find_look_alike_groups_llm_fallback` (call site: paper_split_fallback,
  owner's yes given 2026-09-26) is only asked when the rules found nothing
  -- for freer phrasing a plain "vs" pattern can't catch. It may only name
  phrases already in the question; `find_grounded_phrases` is the safety
  check its output must pass before anything is built from it, the same
  "classify/extract, never invent" rule every other fallback follows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from llm.client import LLMClient
from llm.schemas import LookAlikeGroupOutput
from online.routing.router import MAX_PIECES  # decision 11's one 6-piece cap, shared everywhere it applies

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You find groups of look-alike things a question is comparing or asking
about together (for example two account types, two product versions, two
audiences), so each can be answered separately.

You are given the question. If it asks about two or more similar things
together, return the exact phrase for each one, copied from the question's
own words as closely as you can.

ABSOLUTE RULE
You never invent a phrase that is not already in the question. If the
question asks about only one thing, return an empty list. Guessing a
second thing that isn't really there is a failure; an empty list is a
correct answer.
"""

# "savings vs current accounts" / "savings versus current accounts" /
# "savings or current accounts" -- two single-word look-alikes sharing one
# trailing word, the shape decision 11's own "Loan limit for savings vs
# current accounts" example uses. Deliberately one word each, not a loose
# multi-word span: a looser pattern greedily swallows unrelated leading
# words ("loan limit for savings" instead of just "savings") since
# `re.search` tries the leftmost position that makes the whole pattern
# match, not the shortest one.
_COMPARISON_RE = re.compile(
    r"\b(?P<a>[A-Za-z]+(?:-[A-Za-z]+)*)\s+(?:vs\.?|versus|or)\s+"
    r"(?P<b>[A-Za-z]+(?:-[A-Za-z]+)*)\s+(?P<shared>[A-Za-z]+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PaperSplit:
    pieces: tuple[str, ...]
    phrases: tuple[str, ...]  # what each piece is "about", for the citation/log
    capped: bool


def find_grounded_phrases(question: str, candidate_phrases: Sequence[str]) -> list[str]:
    """Keeps only the phrases that genuinely appear in the question
    (case-insensitive, whole-word). A model proposing groups must have its
    output checked this way before anything is built from it -- the same
    "classify/extract, never invent" rule every other fallback in this
    project follows."""
    lowered = question.lower()
    return [
        phrase for phrase in candidate_phrases
        if re.search(rf"\b{re.escape(phrase.lower())}\b", lowered)
    ]


def detect_plain_comparison(question: str) -> list[str]:
    """The rule-based detector: "X vs Y <shared word>" -> ["X <shared
    word>", "Y <shared word>"]. Returns an empty list if the question
    doesn't match this plain shape -- that is not a failure, it just means
    this question needs the model fallback to find anything (see
    find_look_alike_groups_llm_fallback), or genuinely has nothing to
    split."""
    match = _COMPARISON_RE.search(question)
    if not match:
        return []
    shared = match.group("shared")
    return [f"{match.group('a').strip()} {shared}", f"{match.group('b').strip()} {shared}"]


def split_by_look_alikes(question: str, phrases: Sequence[str], max_pieces: int = MAX_PIECES) -> PaperSplit:
    """Swaps in one look-alike phrase at a time: each piece is the original
    question with the whole comparison replaced by just that one phrase.
    `phrases` must already be grounded (see find_grounded_phrases) -- this
    function does not re-check that itself."""
    capped = len(phrases) > max_pieces
    kept = list(phrases)[:max_pieces]
    match = _COMPARISON_RE.search(question)
    if match:
        span_text = question[match.start():match.end()]
        pieces = tuple(question.replace(span_text, phrase) for phrase in kept)
    else:
        # No detected comparison span to substitute -- fall back to
        # appending each phrase as its own clarified question, still one
        # piece per look-alike, still capped the same way.
        pieces = tuple(f"{question.rstrip('?').rstrip()} (regarding {phrase})?" for phrase in kept)
    return PaperSplit(pieces=pieces, phrases=tuple(kept), capped=capped)


def find_look_alike_groups_llm_fallback(question: str, llm_client: LLMClient) -> list[str]:
    """The model half: only called when detect_plain_comparison found
    nothing. Any bad or unparseable response is treated as "found nothing"
    (an empty list), never an error -- the same safe-default rule every
    other fallback call site follows. Whatever the model proposes is
    additionally run through find_grounded_phrases before being returned,
    so an invented phrase can never reach the caller."""
    payload = {
        "system_prompt": SYSTEM_PROMPT,
        "schema_version": SCHEMA_VERSION,
        "question": question,
    }
    try:
        raw_response = llm_client.complete(payload)
        resolution = LookAlikeGroupOutput.model_validate(raw_response)
    except Exception:
        return []
    return find_grounded_phrases(question, resolution.phrases)


def split_paper_question(question: str, llm_client: Optional[LLMClient] = None) -> Optional[PaperSplit]:
    """Top-level entry point for step 4.3. Tries the free rule-based check
    first, always; only asks the model if that finds nothing and a client
    was actually given -- omitting llm_client costs nothing and reproduces
    the rule-only behaviour exactly. Returns None when there is nothing to
    split, which is a correct outcome, not an error: most questions aren't
    comparing look-alikes at all."""
    phrases = detect_plain_comparison(question)
    if not phrases and llm_client is not None:
        phrases = find_look_alike_groups_llm_fallback(question, llm_client)
    if len(phrases) < 2:
        return None
    return split_by_look_alikes(question, phrases)
