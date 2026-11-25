"""Citation binder. Always deterministic, never calls an LLM -- citations
are looked up from proposition_id metadata, never written by a model
(that's how fabricated sources sneak into RAG systems).

Keyed by which propositions the caller says were used, never by scanning
answer_text for citation-looking substrings.
"""
from __future__ import annotations

from typing import Any, Sequence

from contracts.models import Proposition


def bind_citations(answer_text: str, supporting_propositions: Sequence[Proposition]) -> list[dict[str, Any]]:
    """One citation dict per supporting proposition, in order. answer_text
    is accepted for interface symmetry but never inspected."""
    return [
        {
            "proposition_id": prop.proposition_id,
            "source_uri": prop.metadata.source_uri,
            "filing_type": prop.metadata.filing_type,
            "filing_date": prop.metadata.filing_date,
            "page": prop.metadata.page,
            "provenance_tier": prop.provenance_tier,
        }
        for prop in supporting_propositions
    ]
