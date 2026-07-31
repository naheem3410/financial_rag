"""OCR tier — Path A, conditional.

An OCR model, not an LLM. PaddleOCR with PP-Structure is the intended
engine — chosen because it performs layout analysis and table structure
recognition, not merely line-level text OCR. Plain OCR on a scanned 10-K
yields numbers with no row/column association, which is useless for
financial extraction.

Status on this corpus: every document here is a digital-native SEC filing
with an extractable text layer, so `maybe_ocr` below never dispatches to a
real engine on this corpus. This module is built and tested against a fake
engine (tests/test_ocr.py) so the interface and its trigger condition are
verified now. Report this tier as unexercised on this corpus, never as a
validated OCR capability — no real provider (PaddleOCR or an alternative:
docTR, Surya, a commercial extractor) is wired in, and choosing one is
deferred until a corpus that actually needs OCR is in scope.

`ocr_confidence` is mandatory on every OCR page — OCR-derived figures are
the lowest-confidence input in the system (a misread decimal is a material
misstatement), and the abstention gate needs to treat them more
conservatively.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class OCRPage:
    """One page's OCR output. `tables` retains row/column structure as a
    tuple of tables, each a tuple of rows, each a tuple of cells."""

    page_number: int  # zero-indexed
    text: str
    tables: tuple[tuple[tuple[str, ...], ...], ...]
    ocr_confidence: float


class OCREngine(Protocol):
    """OCR engine interface. A concrete engine (PaddleOCR PP-Structure, or
    an alternative) implements this. Structural typing: any class with a
    matching `extract` method satisfies the Protocol without inheriting
    from it — the seam a real engine plugs into once one is needed."""

    def extract(self, pdf_path: str) -> list[OCRPage]: ...


def maybe_ocr(
    pdf_path: str, has_text_layer: bool, engine: OCREngine
) -> Optional[list[OCRPage]]:
    """OCR fires only when `pdf_path` has no extractable text layer.
    `has_text_layer` is supplied by the caller (the layout/table parser's
    PDF reader) rather than probed here, so this dispatch is testable
    without a PDF-parsing dependency or a real OCR engine. Returns `None`
    — not an empty list — when OCR is skipped, so callers can tell "not
    triggered" apart from "triggered, found nothing".
    """
    if has_text_layer:
        return None
    return engine.extract(pdf_path)
