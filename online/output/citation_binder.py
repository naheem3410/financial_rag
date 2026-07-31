"""Citation binder. Always deterministic, must never call an LLM —
citations are looked up from `proposition_id` metadata, never written by a
model. That's precisely how fabricated sources appear in RAG systems.

Emits one citation per supporting proposition — source (filing type, date,
page, `source_uri`) plus provenance tier per fact, joined deterministically
from `Proposition.metadata`/`Proposition.provenance_tier`. The answer text
itself is never parsed or matched against — binding is keyed by which
propositions were actually used (passed in explicitly by the caller, e.g.
the generator's `used_fff_ids` resolved back to their source propositions),
not by scanning prose for something that looks like a citation.
"""
from __future__ import annotations

from typing import Any, Sequence

from contracts.models import Proposition


def bind_citations(answer_text: str, supporting_propositions: Sequence[Proposition]) -> list[dict[str, Any]]:
    """One citation dict per proposition in `supporting_propositions`, in
    the order given. `answer_text` is accepted for interface symmetry with
    the rest of the output stage (and so a future caller can log it
    alongside the citation set) but is not inspected."""
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
