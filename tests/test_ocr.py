"""OCR tier tests.

FinanceBench never exercises this tier — all 84 documents are
digital-native — so these tests use a fake engine rather than a real OCR
provider. They prove the interface and its trigger condition exist and
behave correctly, even though the capability itself stays unexercised on
this corpus.
"""
from __future__ import annotations

from offline.ocr.paddle_ocr import OCREngine, OCRPage, maybe_ocr


class _FakeOCREngine:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, pdf_path: str) -> list[OCRPage]:
        self.calls.append(pdf_path)
        return [OCRPage(page_number=0, text="scanned page text", tables=(), ocr_confidence=0.87)]


def test_ocr_skipped_when_text_layer_present():
    """The FinanceBench-realistic case: every document is digital-native, so
    OCR must never fire."""
    engine = _FakeOCREngine()

    result = maybe_ocr("digital_native_10k.pdf", has_text_layer=True, engine=engine)

    assert result is None
    assert engine.calls == []


def test_ocr_fires_when_text_layer_absent():
    engine = _FakeOCREngine()

    result = maybe_ocr("scanned_10k.pdf", has_text_layer=False, engine=engine)

    assert result is not None
    assert engine.calls == ["scanned_10k.pdf"]


def test_ocr_page_carries_mandatory_confidence_field():
    engine = _FakeOCREngine()

    pages = maybe_ocr("scanned_10k.pdf", has_text_layer=False, engine=engine)

    assert pages[0].ocr_confidence == 0.87
    assert pages[0].page_number == 0  # zero-indexed


def test_ocr_engine_is_structurally_satisfied_without_inheritance():
    """No provider is wired in — but OCREngine is a Protocol, so any class
    with a matching `extract` method satisfies it. This is the seam a real
    engine (PaddleOCR PP-Structure or an alternative) plugs into later."""
    engine: OCREngine = _FakeOCREngine()

    pages = engine.extract("whatever.pdf")

    assert isinstance(pages, list)
    assert isinstance(pages[0], OCRPage)
