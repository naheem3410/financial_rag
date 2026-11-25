"""Fixed-size Chunker tests (C0 naive RAG baseline only)."""
from __future__ import annotations

from offline.chunking.fixed_size_chunker import chunk_document_fixed_size, chunk_page_fixed_size
from offline.parsers.layout_parser import ParsedPage


def test_short_page_yields_one_chunk_no_padding():
    page = ParsedPage(page_number=0, text="Short page text.", tables=[])
    chunks = chunk_page_fixed_size(page, chunk_size=2000, overlap=200)

    assert chunks == [(0, "Short page text.")]


def test_long_page_slides_with_overlap():
    text = "A" * 5000
    page = ParsedPage(page_number=0, text=text, tables=[])
    chunks = chunk_page_fixed_size(page, chunk_size=2000, overlap=200)

    assert len(chunks) == 3
    offsets = [offset for offset, _ in chunks]
    assert offsets == [0, 1800, 3600]
    # overlap: chunk 1 and chunk 2 share the [1800, 2000) region
    assert chunks[0][1][1800:2000] == chunks[1][1][:200]


def test_long_page_covers_the_whole_text_no_gaps():
    text = "".join(f"{i:04d}" for i in range(2000))  # 8000 deterministic chars
    page = ParsedPage(page_number=0, text=text, tables=[])
    chunks = chunk_page_fixed_size(page, chunk_size=2000, overlap=200)

    last_offset, last_text = chunks[-1]
    assert last_offset + len(last_text) == len(text)  # reaches the end exactly


def test_empty_page_yields_no_chunks():
    page = ParsedPage(page_number=0, text="   ", tables=[])
    assert chunk_page_fixed_size(page) == []


def test_chunk_document_produces_propositions_with_correct_page_and_company():
    pages = [
        ParsedPage(page_number=0, text="Page zero text.", tables=[]),
        ParsedPage(page_number=1, text="Page one text.", tables=[]),
    ]
    props = chunk_document_fixed_size(
        pages, source_uri="3M_2018_10K", company="3M Company",
        filing_type="10-K", filing_date="2018-12-31",
    )

    assert len(props) == 2
    assert {p.metadata.page for p in props} == {0, 1}
    assert all(p.metadata.company == "3M Company" for p in props)
    assert all(p.metadata.source_uri == "3M_2018_10K" for p in props)
    assert all(p.numeric is None for p in props)


def test_chunk_ids_are_deterministic():
    pages = [ParsedPage(page_number=0, text="Same text.", tables=[])]
    a = chunk_document_fixed_size(pages, "doc", "Co", "10-K", "2020-01-01")
    b = chunk_document_fixed_size(pages, "doc", "Co", "10-K", "2020-01-01")

    assert [p.proposition_id for p in a] == [p.proposition_id for p in b]


def test_empty_document_yields_no_chunks():
    pages = [ParsedPage(page_number=0, text="", tables=[])]
    assert chunk_document_fixed_size(pages, "doc", "Co", "10-K", "2020-01-01") == []
