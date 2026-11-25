"""Metadata tagger tests."""
from __future__ import annotations

import pytest

from offline.metadata.tagger import tag


def test_tags_from_document_context():
    document_context = {
        "company": "3M Company",
        "ticker": "MMM",
        "filing_type": "10-K",
        "filing_date": "2019-02-07",
        "source_uri": "3M_2018_10K.pdf",
    }

    metadata = tag(document_context, section="Consolidated Statement of Cash Flows", page=33)

    assert metadata.company == "3M Company"
    assert metadata.ticker == "MMM"
    assert metadata.filing_type == "10-K"
    assert metadata.filing_date == "2019-02-07"
    assert metadata.section == "Consolidated Statement of Cash Flows"
    assert metadata.page == 33
    assert metadata.source_uri == "3M_2018_10K.pdf"


def test_missing_required_key_raises():
    with pytest.raises(ValueError, match="missing required key"):
        tag({"company": "3M Company"})


def test_page_is_zero_indexed_by_convention_of_caller():
    document_context = {
        "company": "3M Company",
        "filing_type": "10-K",
        "filing_date": "2019-02-07",
        "source_uri": "3M_2018_10K.pdf",
    }

    metadata = tag(document_context, page=0)

    assert metadata.page == 0


def test_section_falls_back_to_document_context():
    document_context = {
        "company": "3M Company",
        "filing_type": "10-K",
        "filing_date": "2019-02-07",
        "source_uri": "3M_2018_10K.pdf",
        "section": "MD&A",
    }

    metadata = tag(document_context)

    assert metadata.section == "MD&A"
