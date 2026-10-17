"""Gold-overlap safety assertion tests."""
from __future__ import annotations

import json

import pytest

from offline.gold_overlap import (
    GoldPageSkippedError,
    assert_no_gold_page_overlap,
    find_gold_page_overlaps,
    gold_evidence_pages,
)


def _write_jsonl(tmp_path, records):
    path = tmp_path / "collision.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return path


def test_null_evidence_doc_name_resolves_to_primary_doc(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            {
                "source": {
                    "primary_doc": "3M_2018_10K",
                    "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 59}],
                }
            }
        ],
    )
    assert gold_evidence_pages(path) == {"3M_2018_10K": {59}}


def test_explicit_evidence_doc_name_is_kept_separate_from_primary(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            {
                "source": {
                    "primary_doc": "3M_2018_10K",
                    "evidence_refs": [
                        {"doc_name": None, "page_num_zero_indexed": 59},
                        {"doc_name": "3M_2017_10K", "page_num_zero_indexed": 12},
                    ],
                }
            }
        ],
    )
    assert gold_evidence_pages(path) == {"3M_2018_10K": {59}, "3M_2017_10K": {12}}


def test_pages_accumulate_across_multiple_records(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            {"source": {"primary_doc": "3M_2018_10K", "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 59}]}},
            {"source": {"primary_doc": "3M_2018_10K", "evidence_refs": [{"doc_name": None, "page_num_zero_indexed": 60}]}},
        ],
    )
    assert gold_evidence_pages(path) == {"3M_2018_10K": {59, 60}}


def test_no_overlap_when_skipped_pages_and_gold_pages_are_disjoint():
    gold = {"3M_2018_10K": {59, 60}}
    skipped = {"3M_2018_10K": {9: "risk_factors_item_1a", 10: "risk_factors_item_1a"}}
    assert find_gold_page_overlaps(gold, skipped) == []
    assert_no_gold_page_overlap(gold, skipped)  # must not raise


def test_overlap_is_detected_and_reported():
    gold = {"3M_2018_10K": {9}}
    skipped = {"3M_2018_10K": {9: "risk_factors_item_1a", 10: "risk_factors_item_1a"}}
    overlaps = find_gold_page_overlaps(gold, skipped)
    assert len(overlaps) == 1
    assert overlaps[0].doc_name == "3M_2018_10K"
    assert overlaps[0].page_number == 9
    assert overlaps[0].skip_reason == "risk_factors_item_1a"


def test_overlap_raises_gold_page_skipped_error():
    gold = {"3M_2018_10K": {9}}
    skipped = {"3M_2018_10K": {9: "risk_factors_item_1a"}}
    with pytest.raises(GoldPageSkippedError) as exc_info:
        assert_no_gold_page_overlap(gold, skipped)
    assert "3M_2018_10K page 9" in str(exc_info.value)


def test_documents_with_no_skips_recorded_are_safe_by_construction():
    gold = {"ADOBE_2016_10K": {5}}
    skipped: dict[str, dict[int, str]] = {}  # e.g. doc had zero skipped pages
    assert find_gold_page_overlaps(gold, skipped) == []
