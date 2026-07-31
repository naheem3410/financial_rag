"""Metadata tagger — Path A & B convergence.

No model involved. Fields are drawn from filing headers / XBRL contexts /
file provenance — this module does no inference, no LLM, and no guessing
beyond what is explicitly supplied; a missing required field is a caller
error, not something to default silently.

The corpus supplies `doc_name`, `company`, `doc_type`, and `doc_period`
directly per-question/per-document, so `document_context` here is
populated from those dataset fields rather than parsed from raw SEC filing
header bytes — there is no header-parsing step to build for this corpus.
`page` must already be zero-indexed by the caller (the layout/table
parser's section/page tracking) to match the evaluation data's page
convention.
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
    """Pure field copying/defaulting into the proposition's `metadata` block."""
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
