"""Row-to-proposition templating — Path B. No model involved: a row
already is a proposition, so MappedRow -> Proposition is pure string
templating and field copying.
"""
from __future__ import annotations

import hashlib

from contracts.models import (
    Confidence,
    NumericRecord,
    Period,
    Proposition,
    PropositionMetadata,
)
from offline.parsers.schema_mapper import MappedRow

# Canonical metric id -> human-readable label. Extend as new metrics arrive.
_METRIC_LABELS: dict[str, str] = {
    "operating_margin": "operating margin",
    "net_profit_margin": "net profit margin",
    "gross_margin": "gross margin",
}


def _metric_label(metric: str) -> str:
    return _METRIC_LABELS.get(metric, metric.replace("_", " "))


def _period_label(row: MappedRow) -> str:
    if row.fiscal_year is None:
        return "an unspecified period"
    if row.quarter is not None:
        return f"Q{row.quarter} FY{row.fiscal_year}"
    return f"FY{row.fiscal_year}"


def _proposition_id(row: MappedRow) -> str:
    """Deterministic and stable given the same row — no randomness."""
    key = "|".join(
        str(part)
        for part in (
            row.source_uri,
            row.company,
            row.metric,
            row.fiscal_year,
            row.quarter,
            row.value,
        )
    )
    return "p_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def template_row(row: MappedRow) -> Proposition:
    """e.g. "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."
    Pure templating; the number is copied verbatim from row.value."""
    text = (
        f"{row.company} {_metric_label(row.metric)} for {_period_label(row)} "
        f"was {row.value} {row.unit}."
    )

    numeric = None
    try:
        numeric_value = float(row.value)
    except (TypeError, ValueError):
        numeric_value = None
    if numeric_value is not None and row.fiscal_year is not None:
        numeric = NumericRecord(
            value=numeric_value,
            scale=row.scale,
            currency=row.currency,
            unit=row.unit,
            period=Period(fiscal_year=row.fiscal_year, quarter=row.quarter, basis=row.basis),
        )

    return Proposition(
        proposition_id=_proposition_id(row),
        text=text,
        provenance_tier="retrieved",
        ingestion_path="B_structured",
        numeric=numeric,
        confidence=Confidence(
            ocr_confidence=1.0,
            extraction_tier="xbrl" if row.source_format == "xbrl" else "rules",
        ),
        metadata=PropositionMetadata(
            company=row.company,
            ticker=row.ticker,
            filing_type=row.filing_type,
            filing_date=row.filing_date,
            section=row.section,
            page=row.page,
            source_uri=row.source_uri,
        ),
    )


def template_rows(rows: list[MappedRow]) -> list[Proposition]:
    return [template_row(row) for row in rows]
