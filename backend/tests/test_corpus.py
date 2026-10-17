"""FinanceBench corpus scope tests. Unit tests use a small synthetic fixture;
integration tests use the real jsonl, required_docs.txt, and pdfs/ in this repo."""
from __future__ import annotations

from pathlib import Path

import pytest

from offline.corpus import assert_matches_required_docs, parse_doc_name, required_doc_names, resolve_pdf_paths

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "corpus"
REPO_ROOT = Path(__file__).resolve().parent.parent


def test_required_doc_names_dedupes_across_questions():
    """Two questions in the fixture share doc_name "3M_2018_10K", so the
    result must dedupe to distinct documents, not one entry per question."""
    names = required_doc_names(FIXTURES / "sample_financebench.jsonl")

    assert names == {"3M_2018_10K", "ADOBE_2015_10K"}


def test_assert_matches_required_docs_passes_on_exact_match():
    names = required_doc_names(FIXTURES / "sample_financebench.jsonl")

    assert_matches_required_docs(names, FIXTURES / "sample_required_docs.txt")  # must not raise


def test_assert_matches_required_docs_raises_on_missing_document():
    names = {"3M_2018_10K"}  # ADOBE_2015_10K missing

    with pytest.raises(ValueError, match="does not match"):
        assert_matches_required_docs(names, FIXTURES / "sample_required_docs.txt")


def test_assert_matches_required_docs_raises_on_extra_document():
    names = {"3M_2018_10K", "ADOBE_2015_10K", "UNEXPECTED_2020_10K"}

    with pytest.raises(ValueError, match="does not match"):
        assert_matches_required_docs(names, FIXTURES / "sample_required_docs.txt")


def test_resolve_pdf_paths_maps_doc_name_to_dot_pdf(tmp_path):
    (tmp_path / "3M_2018_10K.pdf").write_bytes(b"%PDF-1.4 fake")

    resolved = resolve_pdf_paths({"3M_2018_10K"}, tmp_path)

    assert resolved == {"3M_2018_10K": tmp_path / "3M_2018_10K.pdf"}


def test_resolve_pdf_paths_raises_loudly_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing"):
        resolve_pdf_paths({"DOES_NOT_EXIST_10K"}, tmp_path)


def test_all_required_docs_have_a_matching_pdf_file():
    """required_docs.txt and pdfs/ exist even without the jsonl that derives
    the list; confirms resolution works ahead of the full pipeline landing."""
    required_docs_path = REPO_ROOT / "data" / "required_docs.txt"
    pdf_dir = REPO_ROOT / "data" / "pdfs"
    if not required_docs_path.exists() or not pdf_dir.exists():
        pytest.skip("data/required_docs.txt or data/pdfs/ not present in this environment")

    doc_names = {
        line.strip() for line in required_docs_path.read_text(encoding="utf-8").splitlines() if line.strip()
    }
    assert len(doc_names) == 84

    resolved = resolve_pdf_paths(doc_names, pdf_dir)

    assert len(resolved) == 84


def test_real_financebench_jsonl_derives_and_matches_required_docs():
    """Full real pipeline end to end: derive, assert, and resolve against
    the actual jsonl, required_docs.txt, and pdfs/."""
    jsonl_path = REPO_ROOT / "evaluation" / "data" / "financebench_open_source.jsonl"
    required_docs_path = REPO_ROOT / "data" / "required_docs.txt"
    pdf_dir = REPO_ROOT / "data" / "pdfs"
    if not jsonl_path.exists():
        pytest.skip("evaluation/data/financebench_open_source.jsonl not present in this environment")

    names = required_doc_names(jsonl_path)
    assert len(names) == 84

    assert_matches_required_docs(names, required_docs_path)  # must not raise

    resolved = resolve_pdf_paths(names, pdf_dir)
    assert len(resolved) == 84


class TestParseDocName:
    def test_extracts_fiscal_year_and_filing_type_from_10k(self):
        assert parse_doc_name("3M_2018_10K") == {"fiscal_year": 2018, "filing_type": "10-K"}

    def test_extracts_quarter_from_10q(self):
        assert parse_doc_name("3M_2023Q2_10Q") == {"fiscal_year": 2023, "quarter": 2, "filing_type": "10-Q"}

    def test_extracts_fiscal_year_and_filing_type_from_8k(self):
        result = parse_doc_name("FOOTLOCKER_2022_8K_dated-2022-05-20")
        assert result == {"fiscal_year": 2022, "filing_type": "8-K"}

    def test_unmatched_pattern_returns_empty_dict(self):
        assert parse_doc_name("JOHNSON_JOHNSON_2022Q4_EARNINGS") == {}

    def test_covers_all_84_required_docs_without_crashing(self):
        required_docs_path = REPO_ROOT / "data" / "required_docs.txt"
        if not required_docs_path.exists():
            pytest.skip("data/required_docs.txt not present in this environment")

        names = [
            line.strip() for line in required_docs_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        results = {name: parse_doc_name(name) for name in names}

        matched = [name for name, r in results.items() if r]
        assert len(matched) >= 70  # most of the 84 match; a documented minority (EARNINGS) do not
