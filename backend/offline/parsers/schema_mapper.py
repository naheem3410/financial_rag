"""Schema mapper — Path B ingestion. No model involved here or in
row_templating.py downstream (enforced by test_no_direct_sdk_imports.py).

Three formats converge on one canonical MappedRow before row_templating.py
turns each into a Proposition:
- CSV — csv.DictReader, header row supplies field names.
- XLSX — openpyxl, first sheet, first row is header.
- XBRL — stdlib ElementTree; resolves context/unit refs, dei:* elements
  supply doc-level context. Simplified (no decimals/scale normalization),
  validated only against synthetic fixtures — current corpus never hits Path B.

map_schema itself is format-agnostic: normalizes any dict onto the
canonical field names below.
"""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, Union

import openpyxl

SourceFormat = Literal["csv", "xlsx", "xbrl"]

# Column/tag synonyms seen in the wild -> canonical field name.
# Extend as new synonyms turn up.
_FIELD_SYNONYMS: dict[str, str] = {
    "company": "company",
    "company_name": "company",
    "issuer": "company",
    "entity": "company",
    "ticker": "ticker",
    "symbol": "ticker",
    "metric": "metric",
    "line_item": "metric",
    "concept": "metric",
    "tag": "metric",
    "fiscal_year": "fiscal_year",
    "fy": "fiscal_year",
    "year": "fiscal_year",
    "quarter": "quarter",
    "fq": "quarter",
    "period_quarter": "quarter",
    "basis": "basis",
    "value": "value",
    "amount": "value",
    "unit": "unit",
    "units": "unit",
    "scale": "scale",
    "currency": "currency",
    "filing_type": "filing_type",
    "form_type": "filing_type",
    "doc_type": "filing_type",
    "filing_date": "filing_date",
    "date": "filing_date",
    "section": "section",
    "page": "page",
    "source_uri": "source_uri",
    "source_file": "source_uri",
    "file": "source_uri",
}

_REQUIRED_FIELDS = {"company", "metric", "value", "filing_date"}

# Raw unit tokens -> NumericRecord.unit enum. Unrecognized defaults to
# "absolute" rather than raising, since dollar amounts are the common case.
_UNIT_ALIASES: dict[str, str] = {
    "percent": "percent",
    "%": "percent",
    "pct": "percent",
    "ratio": "ratio",
    "usd": "absolute",
    "dollars": "absolute",
    "$": "absolute",
    "absolute": "absolute",
    "count": "count",
    "shares": "count",
}


def _normalize_unit(raw: str) -> str:
    return _UNIT_ALIASES.get(raw.strip().lower(), "absolute")


@dataclass(frozen=True)
class MappedRow:
    """Canonical view of one structured-input row, pre-templating."""

    company: str
    metric: str
    value: str
    filing_date: str
    ticker: Optional[str] = None
    fiscal_year: Optional[int] = None
    quarter: Optional[int] = None
    basis: str = "reported"
    unit: str = "absolute"
    scale: str = "unit"
    currency: Optional[str] = None
    filing_type: str = "csv"
    section: str = ""
    page: int = 0
    source_uri: str = ""
    source_format: SourceFormat = "csv"


def map_schema(
    row: dict[str, Any],
    source_format: SourceFormat,
    document_context: Optional[dict[str, Any]] = None,
) -> MappedRow:
    """Normalize one row (+ optional shared doc context) to a MappedRow.
    Pure dict/string manipulation, no model."""
    document_context = document_context or {}
    merged: dict[str, Any] = {}
    for raw_key, raw_value in {**document_context, **row}.items():
        if raw_value is None or raw_value == "":
            continue
        canonical = _FIELD_SYNONYMS.get(str(raw_key).strip().lower())
        if canonical is not None:
            merged[canonical] = raw_value

    if merged.get("filing_type") is None:
        if source_format in ("csv", "xlsx"):
            merged["filing_type"] = source_format
        else:
            raise ValueError(
                "schema_mapper: xbrl rows must specify filing_type explicitly "
                "— XBRL tags a real filing, it is not a filing_type itself "
                f"(row={row!r})"
            )

    missing = _REQUIRED_FIELDS - merged.keys()
    if missing:
        raise ValueError(
            f"schema_mapper: row missing required field(s) {sorted(missing)}: {row!r}"
        )

    fiscal_year = merged.get("fiscal_year")
    quarter = merged.get("quarter")

    return MappedRow(
        company=str(merged["company"]),
        metric=str(merged["metric"]),
        value=str(merged["value"]),
        filing_date=str(merged["filing_date"]),
        ticker=str(merged["ticker"]) if merged.get("ticker") is not None else None,
        fiscal_year=int(fiscal_year) if fiscal_year not in (None, "") else None,
        quarter=int(quarter) if quarter not in (None, "") else None,
        basis=str(merged.get("basis", "reported")),
        unit=_normalize_unit(str(merged.get("unit", "absolute"))),
        scale=str(merged.get("scale", "unit")),
        currency=str(merged["currency"]) if merged.get("currency") is not None else None,
        filing_type=str(merged["filing_type"]),
        section=str(merged.get("section", "")),
        page=int(merged.get("page", 0) or 0),
        source_uri=str(merged.get("source_uri", "")),
        source_format=source_format,
    )


def read_csv_rows(path: Union[str, Path]) -> list[dict[str, Any]]:
    """CSV reader; header row supplies field names."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_xlsx_rows(path: Union[str, Path]) -> list[dict[str, Any]]:
    """XLSX reader; first sheet, first row is the header."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = [str(h) for h in next(rows)]
    return [
        dict(zip(header, row)) for row in rows if any(cell is not None for cell in row)
    ]


_XBRL_CONCEPT_TO_METRIC: dict[str, str] = {
    "OperatingIncomeLossMargin": "operating_margin",
    "GrossMarginPercentage": "gross_margin",
    "NetIncomeLossMargin": "net_profit_margin",
}

_MONTH_TO_QUARTER: dict[int, int] = {3: 1, 6: 2, 9: 3, 12: 4}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_xbrl_facts(path: Union[str, Path]) -> list[dict[str, Any]]:
    """Minimal XBRL instance reader (stdlib only). See module docstring
    for what's simplified away."""
    root = ET.parse(path).getroot()

    contexts: dict[str, dict[str, Any]] = {}
    for ctx in root.iter():
        if _local_name(ctx.tag) != "context":
            continue
        entity_identifier, end_date = None, None
        for child in ctx.iter():
            local = _local_name(child.tag)
            if local == "identifier":
                entity_identifier = (child.text or "").strip()
            elif local == "endDate":
                end_date = (child.text or "").strip()
        contexts[ctx.get("id")] = {
            "entity_identifier": entity_identifier,
            "end_date": end_date,
        }

    units: dict[str, str] = {}
    for unit in root.iter():
        if _local_name(unit.tag) != "unit":
            continue
        measure = next(
            ((child.text or "").strip() for child in unit if _local_name(child.tag) == "measure"),
            "",
        )
        units[unit.get("id")] = measure

    doc_context: dict[str, Any] = {}
    for el in root:
        local = _local_name(el.tag)
        if local == "EntityRegistrantName":
            doc_context["company"] = (el.text or "").strip()
        elif local == "DocumentType":
            doc_context["filing_type"] = (el.text or "").strip()
        elif local == "DocumentPeriodEndDate":
            doc_context["filing_date"] = (el.text or "").strip()

    facts: list[dict[str, Any]] = []
    for el in root:
        local = _local_name(el.tag)
        namespace = el.tag.split("}")[0].strip("{") if "}" in el.tag else ""
        if "us-gaap" not in namespace:
            continue

        ctx = contexts.get(el.get("contextRef"), {})
        end_date = ctx.get("end_date")
        fiscal_year, quarter = None, None
        if end_date:
            year_str, month_str, _ = end_date.split("-")
            fiscal_year = int(year_str)
            quarter = _MONTH_TO_QUARTER.get(int(month_str))

        row = dict(doc_context)
        row.update(
            {
                "company": doc_context.get("company") or ctx.get("entity_identifier", ""),
                "metric": _XBRL_CONCEPT_TO_METRIC.get(local, local),
                "value": (el.text or "").strip(),
                "unit": units.get(el.get("unitRef"), ""),
                "fiscal_year": fiscal_year,
                "quarter": quarter,
            }
        )
        facts.append(row)
    return facts
