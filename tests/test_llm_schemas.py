"""Tests for llm/schemas.py — numeric qualifier resolution output
validation, field-scoped rather than a blanket digit scan.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm.schemas import NumericQualifierResolution


def test_fiscal_year_digit_is_a_legitimate_declared_field():
    resolution = NumericQualifierResolution(
        reasoning="The prior period refers to the previous fiscal year end.",
        fiscal_year=2017,
        quarter=None,
        basis="reported",
        scale=None,
        currency="USD",
        resolvable=True,
    )

    assert resolution.fiscal_year == 2017


def test_digit_in_reasoning_is_rejected():
    with pytest.raises(ValidationError, match="digits"):
        NumericQualifierResolution(
            reasoning="This refers to fiscal year 2017.",  # digit leaked into free text
            fiscal_year=2017,
            resolvable=True,
        )


def test_undeclared_field_is_rejected():
    with pytest.raises(ValidationError):
        NumericQualifierResolution.model_validate(
            {
                "reasoning": "Resolved from context.",
                "fiscal_year": 2017,
                "resolvable": True,
                "value": "42",  # not part of the declared schema
            }
        )


def test_quarter_outside_enumeration_is_rejected():
    with pytest.raises(ValidationError):
        NumericQualifierResolution.model_validate(
            {"reasoning": "Resolved from context.", "quarter": 5, "resolvable": True}
        )


def test_unresolvable_qualifier_is_a_valid_response():
    resolution = NumericQualifierResolution(
        reasoning="Context does not indicate which period this refers to.",
        resolvable=False,
    )

    assert resolution.resolvable is False
    assert resolution.fiscal_year is None
