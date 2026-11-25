"""Naive generator tests (C0 only)."""
from __future__ import annotations

from contracts.models import Confidence, Proposition, PropositionMetadata
from online.output.naive_generator import generate_naive_answer


def _chunk(cid: str, text: str) -> Proposition:
    return Proposition(
        proposition_id=cid,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="3M Company", filing_type="10-K", filing_date="2018-12-31",
            section="", page=57, source_uri="3M_2018_10K",
        ),
    )


class _FakeLLMClient:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._response


def test_no_chunks_abstains_without_calling_the_llm():
    client = _FakeLLMClient({})
    result = generate_naive_answer("What were 3M's net sales?", [], client)

    assert result.answerable is False
    assert client.calls == []


def test_answerable_response_produces_answers_and_used_chunk_ids():
    chunk = _chunk("c_1", "3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    client = _FakeLLMClient({
        "reasoning": "...", "answerable": True,
        "answers": [{"metric": "net_sales", "answer_value": "$32,765 million", "answer_unit": "millions"}],
        "answer_text": "3M's net sales were $32,765 million.",
        "used_chunk_ids": ["c_1"],
    })

    result = generate_naive_answer("What were 3M's net sales?", [chunk], client)

    assert result.answerable is True
    assert result.answers[0].value == "$32,765 million"
    assert result.used_chunk_ids == ["c_1"]


def test_not_answerable_response_abstains():
    chunk = _chunk("c_1", "unrelated passage")
    client = _FakeLLMClient({"reasoning": "...", "answerable": False, "answer_text": "not found", "answers": [], "used_chunk_ids": []})

    result = generate_naive_answer("What were 3M's net sales?", [chunk], client)

    assert result.answerable is False
    assert result.answer_text == "not found"


def test_unreferenced_chunk_id_is_filtered_out():
    """Unreferenced chunk ids get dropped -- the one bookkeeping check C0
    keeps, separate from the numeric-verbatim guard C1-C4 have."""
    chunk = _chunk("c_1", "3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    client = _FakeLLMClient({
        "reasoning": "...", "answerable": True,
        "answers": [{"metric": "net_sales", "answer_value": "$32,765 million", "answer_unit": "millions"}],
        "answer_text": "3M's net sales were $32,765 million.",
        "used_chunk_ids": ["c_1", "c_nonexistent"],
    })

    result = generate_naive_answer("What were 3M's net sales?", [chunk], client)

    assert result.used_chunk_ids == ["c_1"]


def test_no_numeric_verbatim_guard_a_fabricated_value_passes_through():
    """C0 has no guard checking the asserted value appears in a chunk --
    that's the baseline C1-C4's guards are measured against, not a bug."""
    chunk = _chunk("c_1", "3M Company had net sales of $32,765 million in the year ended December 31, 2018.")
    client = _FakeLLMClient({
        "reasoning": "...", "answerable": True,
        "answers": [{"metric": "net_sales", "answer_value": "$99,999 million", "answer_unit": "millions"}],  # not in the chunk
        "answer_text": "3M's net sales were $99,999 million.",
        "used_chunk_ids": ["c_1"],
    })

    result = generate_naive_answer("What were 3M's net sales?", [chunk], client)

    assert result.answerable is True
    assert result.answers[0].value == "$99,999 million"  # passed through unchecked
