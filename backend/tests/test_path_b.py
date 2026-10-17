"""Path B unit tests -- exercise the mapping/templating modules directly.
test_path_b_no_llm covers the same modules end-to-end instead."""
from __future__ import annotations

from pathlib import Path

import pytest

from offline.parsers.schema_mapper import (
    MappedRow,
    map_schema,
    read_csv_rows,
    read_xbrl_facts,
    read_xlsx_rows,
)
from offline.propositions.row_templating import template_row, template_rows

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "path_b"


def test_map_schema_normalizes_synonyms():
    row = {
        "Company": "Acme Corp",
        "Ticker": "ACME",
        "Line_Item": "operating_margin",
        "FY": "2024",
        "FQ": "3",
        "Amount": "18.2",
        "Units": "percent",
        "Form_Type": "10-Q",
        "Date": "2024-11-05",
        "File": "acme.csv",
    }

    mapped = map_schema(row, source_format="csv")

    assert mapped.company == "Acme Corp"
    assert mapped.ticker == "ACME"
    assert mapped.metric == "operating_margin"
    assert mapped.fiscal_year == 2024
    assert mapped.quarter == 3
    assert mapped.value == "18.2"
    assert mapped.unit == "percent"
    assert mapped.filing_type == "10-Q"
    assert mapped.filing_date == "2024-11-05"
    assert mapped.source_uri == "acme.csv"


def test_map_schema_defaults_filing_type_for_csv_and_xlsx():
    row = {
        "company": "Acme Corp",
        "metric": "operating_margin",
        "value": "18.2",
        "filing_date": "2024-11-05",
    }

    assert map_schema(row, source_format="csv").filing_type == "csv"
    assert map_schema(row, source_format="xlsx").filing_type == "xlsx"


def test_map_schema_requires_filing_type_for_xbrl():
    row = {
        "company": "Acme Corp",
        "metric": "operating_margin",
        "value": "18.2",
        "filing_date": "2024-11-05",
    }

    with pytest.raises(ValueError, match="filing_type"):
        map_schema(row, source_format="xbrl")


def test_map_schema_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="missing required field"):
        map_schema({"company": "Acme Corp"}, source_format="csv")


def test_map_schema_normalizes_unrecognized_unit_to_absolute():
    row = {
        "company": "Acme Corp",
        "metric": "net_sales",
        "value": "8500000000",
        "unit": "widgets",
        "filing_date": "2024-11-05",
    }

    assert map_schema(row, source_format="csv").unit == "absolute"


def test_template_row_copies_the_value_verbatim():
    row = MappedRow(
        company="Acme Corp",
        metric="operating_margin",
        value="18.2",
        filing_date="2024-11-05",
        fiscal_year=2024,
        quarter=3,
        unit="percent",
        filing_type="10-Q",
        source_uri="acme.csv",
    )

    prop = template_row(row)

    assert prop.text == "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."
    assert prop.provenance_tier == "retrieved"
    assert prop.ingestion_path == "B_structured"
    assert prop.numeric.value == 18.2
    assert prop.numeric.period.fiscal_year == 2024
    assert prop.numeric.period.quarter == 3
    assert prop.confidence.extraction_tier == "rules"
    assert prop.metadata.company == "Acme Corp"
    assert prop.metadata.filing_date == "2024-11-05"


def test_template_row_xbrl_source_gets_xbrl_extraction_tier():
    row = MappedRow(
        company="Acme Corp",
        metric="gross_margin",
        value="21.5",
        filing_date="2024-09-30",
        fiscal_year=2024,
        quarter=3,
        unit="percent",
        filing_type="10-Q",
        source_format="xbrl",
    )

    assert template_row(row).confidence.extraction_tier == "xbrl"


def test_proposition_id_is_deterministic():
    row = MappedRow(
        company="Acme Corp",
        metric="operating_margin",
        value="18.2",
        filing_date="2024-11-05",
        fiscal_year=2024,
        quarter=3,
        source_uri="acme.csv",
    )

    assert template_row(row).proposition_id == template_row(row).proposition_id


def test_csv_ingestion_end_to_end():
    rows = read_csv_rows(FIXTURES / "metrics.csv")
    mapped = [map_schema(r, source_format="csv") for r in rows]
    propositions = template_rows(mapped)

    assert len(propositions) == 3
    acme_operating = next(
        p for p in propositions if p.metadata.company == "Acme Corp" and "operating margin" in p.text
    )
    assert acme_operating.text == "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."
    assert acme_operating.numeric.value == 18.2
    assert acme_operating.metadata.source_uri == "acme_10q_2024q3.csv"


def test_xlsx_ingestion_end_to_end():
    rows = read_xlsx_rows(FIXTURES / "metrics.xlsx")
    mapped = [map_schema(r, source_format="xlsx") for r in rows]
    propositions = template_rows(mapped)

    assert len(propositions) == 2
    beta_gross = next(p for p in propositions if "gross margin" in p.text)
    assert beta_gross.numeric.value == 34.7
    assert beta_gross.metadata.filing_type == "10-Q"


def test_xbrl_ingestion_end_to_end():
    facts = read_xbrl_facts(FIXTURES / "facts.xbrl")
    mapped = [map_schema(f, source_format="xbrl") for f in facts]
    propositions = template_rows(mapped)

    assert len(propositions) == 1
    fact = propositions[0]
    assert fact.metadata.company == "Acme Corp"
    assert fact.text == "Acme Corp gross margin for Q3 FY2024 was 21.5 percent."
    assert fact.numeric.value == 21.5
    assert fact.numeric.period.fiscal_year == 2024
    assert fact.numeric.period.quarter == 3
    assert fact.confidence.extraction_tier == "xbrl"
    assert fact.metadata.filing_type == "10-Q"
