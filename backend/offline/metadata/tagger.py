"""Metadata tagger — Path A & B convergence.

No model, no inference — just copies fields from filing headers/XBRL/file
provenance. A missing required field is a caller error, not a silent default.
document_context comes straight from the dataset's per-question fields, and
page must already be zero-indexed by the caller.
"""
from __future__ import annotations

from typing import Any, Optional

from contracts.models import PropositionMetadata

_REQUIRED_KEYS = {"company", "filing_type", "filing_date", "source_uri"}


def tag(
    document_context: dict[str, Any],
    section: str = "",
    page: int = 0,
    ticker: Optional[str] = None,
) -> PropositionMetadata:
    """Copies/defaults fields into the proposition's metadata block."""
    missing = _REQUIRED_KEYS - document_context.keys()
    if missing:
        raise ValueError(
            f"metadata_tagger: document_context missing required key(s) {sorted(missing)}: "
            f"{document_context!r}"
        )

    return PropositionMetadata(
        company=str(document_context["company"]),
        ticker=ticker or document_context.get("ticker"),
        filing_type=document_context["filing_type"],
        filing_date=str(document_context["filing_date"]),
        section=section or str(document_context.get("section", "")),
        page=page,
        source_uri=str(document_context["source_uri"]),
    )
