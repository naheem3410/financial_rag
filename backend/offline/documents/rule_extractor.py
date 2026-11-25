"""Splits one rule block's text into its consequent (the main rule) and its
exceptions (carve-outs and amendments introduced by a connector word like
"however" or "except"). Rules only, no model.

Antecedent (who/when a rule applies to) is genuinely harder -- reliably
telling "who this applies to" apart from "what it requires" needs more than
a keyword split. `extract_antecedent_llm_fallback` (owner's yes given
2026-09-26) asks a model for it, but never trusts the answer blindly: the
phrase it returns must share real words with the block's own text, or it is
dropped and the antecedent stays None -- the same "classify/extract, never
invent" rule every other fallback call site in this project follows.
"""
from __future__ import annotations

import re
from typing import Optional

from contracts.models import PolicyFact
from llm.client import LLMClient
from llm.schemas import AntecedentExtractionOutput
from offline.documents.policy_chunker import RuleBlockDraft

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You find who or when a policy rule applies to, from one rule block of text.

You are given the rule block. Find the phrase, already present in the text,
that says who the rule applies to or under what circumstance (for example
"Applicants under the age of 18", "employees hired after 2020"). Quote it as
closely to the source wording as you can.

ABSOLUTE RULE
You never add a condition the text does not already state. If the block
names no particular antecedent -- it applies to everyone, unconditionally --
return null. Guessing one is a failure; null is a correct answer.
"""
_WORD_RE = re.compile(r"[a-z0-9]+")
_MIN_GROUNDING_OVERLAP = 0.5

# Longer, more specific phrases first so "does not apply to" isn't cut short
# by a bare word inside it.
_EXCEPTION_CONNECTORS = (
    "does not apply to",
    "shall not apply to",
    "except",
    "unless",
    "excluding",
    "however",
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;])\s+")
# An optional short lead-in pronoun ("This does not apply to...") is
# stripped along with the connector -- a fixed-width regex lookbehind can't
# span a variable-length word, so sentences are split first and each one
# is then checked from its own start instead.
_EXCEPTION_START_RE = re.compile(
    r"^(?:(?:This|It|That|These)\s+)?(?:" + "|".join(re.escape(c) for c in _EXCEPTION_CONNECTORS) + r")\b[,\s]*",
    re.IGNORECASE,
)


def split_consequent_and_exceptions(text: str) -> tuple[str, tuple[str, ...]]:
    """The first sentence is always the consequent, whatever it says. Every
    later sentence that starts with a connector (an optional short pronoun
    allowed first) becomes an exception, verbatim, with the connector and
    any lead-in pronoun stripped. No connector found -> the whole text is
    the consequent and there are no exceptions."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    if not sentences:
        return text.strip(), ()

    consequent_sentences = [sentences[0]]
    exceptions: list[str] = []
    for sentence in sentences[1:]:
        match = _EXCEPTION_START_RE.match(sentence)
        if match:
            exceptions.append(sentence[match.end():].strip())
        else:
            consequent_sentences.append(sentence)

    return " ".join(consequent_sentences), tuple(exceptions)


def _is_grounded_in_source(phrase: str, source_text: str) -> bool:
    """At least half the antecedent's real words must actually appear in
    the block it was supposedly drawn from -- a cheap, honest check against
    the model inventing a condition the text never stated."""
    phrase_words = set(_WORD_RE.findall(phrase.lower()))
    if not phrase_words:
        return False
    source_words = set(_WORD_RE.findall(source_text.lower()))
    overlap = len(phrase_words & source_words) / len(phrase_words)
    return overlap >= _MIN_GROUNDING_OVERLAP


def extract_antecedent_llm_fallback(text: str, llm_client: LLMClient) -> Optional[str]:
    """Asks the model to find who/when a rule block applies to. `None` from
    the model is trusted as-is (a correct "no particular antecedent"
    answer); a non-empty phrase is only kept if it is grounded in the
    block's own words -- otherwise it is dropped, not trusted."""
    payload = {"schema_version": SCHEMA_VERSION, "system_prompt": SYSTEM_PROMPT, "rule_block": text}
    raw_response = llm_client.complete(payload)

    try:
        resolution = AntecedentExtractionOutput.model_validate(raw_response)
    except Exception:
        return None

    if not resolution.antecedent:
        return None
    if not _is_grounded_in_source(resolution.antecedent, text):
        return None
    return resolution.antecedent


def rule_block_to_fact(
    block: RuleBlockDraft, fact_id: str, document_id: str, source_uri: str, llm_client: Optional[LLMClient] = None
) -> PolicyFact:
    """`llm_client` left out (the default) costs nothing and behaves
    exactly as before this fallback existed: antecedent stays None."""
    consequent, exceptions = split_consequent_and_exceptions(block.text)
    antecedent = extract_antecedent_llm_fallback(block.text, llm_client) if llm_client is not None else None
    return PolicyFact(
        fact_id=fact_id, document_id=document_id, text=block.text,
        section_path=block.section_path, section_number=block.section_number,
        antecedent=antecedent, consequent=consequent, exceptions=exceptions,
        page=block.page, source_uri=source_uri,
    )
