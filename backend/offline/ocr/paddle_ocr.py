"""OCR tier — Path A, conditional.

An OCR model, not an LLM. PaddleOCR/PP-Structure does layout + table
structure recognition, since plain line OCR loses row/column association.
Every doc in this corpus has a text layer, so maybe_ocr never dispatches to
a real engine here — tested against a fake engine only; report unexercised,
not validated. ocr_confidence is mandatory since a misread decimal is a
material misstatement and abstention needs to weigh that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class OCRPage:
    """One page's OCR output; tables keep row/column structure as nested tuples."""

    page_number: int  # zero-indexed
    text: str
    tables: tuple[tuple[tuple[str, ...], ...], ...]
    ocr_confidence: float


class OCREngine(Protocol):
    """OCR engine interface — structural typing, so any class with a
    matching extract() satisfies it without inheriting from this."""

    def extract(self, pdf_path: str) -> list[OCRPage]: ...


def maybe_ocr(
    pdf_path: str, has_text_layer: bool, engine: OCREngine
) -> Optional[list[OCRPage]]:
    """Fires only when has_text_layer is False (supplied by caller, not probed
    here, so this is testable without a PDF dep). Returns None, not [], when
    skipped, so callers can tell "not triggered" from "found nothing"."""
    if has_text_layer:
        return None
    return engine.extract(pdf_path)
