"""ragas_bridge.py tests -- the main-environment half of the RAGAS bridge
(KNOWN_ISSUES.md decision 20). Zero LLM calls anywhere here: this module
never imports `ragas` or touches the network, it only reads/writes plain
JSON, so there is nothing to monkeypatch and nothing a shared fixture would
buy. Proves the write-out contract (shape `score_with_ragas.py` expects)
and that malformed input/output fails loudly rather than silently.
"""
from __future__ import annotations

import json
import sys
import textwrap

import pytest

from evaluation.ragas_bridge import (
    RagasInputRecord,
    read_ragas_scores,
    run_ragas_scoring,
    write_ragas_input,
)

GOOD_RECORDS = [
    {
        "collision_id": "c1",
        "question": "What was revenue in FY2023?",
        "answer": "Revenue was $10.2 billion.",
        "contexts": ["Total revenue for fiscal 2023 was $10.2 billion.", "Net income was $1.1 billion."],
        "reference": "$10.2 billion",
    },
    {
        "collision_id": "c2",
        "question": "What was operating margin?",
        "answer": "Operating margin was 22%.",
        "contexts": ["Operating margin for the period was 22%."],
        # no "reference" -- optional, defaults to ""
    },
]


def test_write_ragas_input_shape(tmp_path):
    out = tmp_path / "ragas_input.json"
    write_ragas_input(GOOD_RECORDS, out)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert isinstance(data["items"], list)
    assert len(data["items"]) == 2

    item0 = data["items"][0]
    assert item0["collision_id"] == "c1"
    assert item0["question"] == GOOD_RECORDS[0]["question"]
    assert item0["answer"] == GOOD_RECORDS[0]["answer"]
    assert item0["contexts"] == GOOD_RECORDS[0]["contexts"]
    assert item0["reference"] == "$10.2 billion"

    # record with no "reference" key defaults to "" in the written file
    item1 = data["items"][1]
    assert item1["reference"] == ""


def test_write_ragas_input_rejects_empty_records(tmp_path):
    with pytest.raises(ValueError):
        write_ragas_input([], tmp_path / "out.json")


@pytest.mark.parametrize(
    "bad_record,expected_fragment",
    [
        ({"question": "q", "answer": "a", "contexts": ["c"]}, "collision_id"),
        ({"collision_id": "c1", "answer": "a", "contexts": ["c"]}, "question"),
        ({"collision_id": "c1", "question": "q", "contexts": ["c"]}, "answer"),
        ({"collision_id": "c1", "question": "q", "answer": "a"}, "contexts"),
        ({"collision_id": "c1", "question": "q", "answer": "a", "contexts": []}, "contexts"),
        ({"collision_id": "c1", "question": "q", "answer": "a", "contexts": "not-a-list"}, "contexts"),
        ({"collision_id": "c1", "question": "q", "answer": "a", "contexts": [1, 2]}, "contexts"),
        ({"collision_id": 1, "question": "q", "answer": "a", "contexts": ["c"]}, "collision_id"),
    ],
)
def test_write_ragas_input_rejects_malformed_records(tmp_path, bad_record, expected_fragment):
    with pytest.raises(ValueError, match=expected_fragment):
        write_ragas_input([bad_record], tmp_path / "out.json")


def test_read_ragas_scores_round_trip(tmp_path):
    scores_path = tmp_path / "scores.json"
    payload = {
        "schema_version": 1,
        "items": [
            {"collision_id": "c1", "faithfulness": 0.9, "answer_relevancy": 0.85},
            {"collision_id": "c2", "faithfulness": 0.5, "answer_relevancy": 0.7},
        ],
    }
    scores_path.write_text(json.dumps(payload), encoding="utf-8")

    items = read_ragas_scores(scores_path)
    assert len(items) == 2
    assert items[0]["collision_id"] == "c1"
    assert items[0]["faithfulness"] == pytest.approx(0.9)
    assert items[1]["answer_relevancy"] == pytest.approx(0.7)


def test_read_ragas_scores_rejects_missing_items_key(tmp_path):
    scores_path = tmp_path / "scores.json"
    scores_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="items"):
        read_ragas_scores(scores_path)


def test_read_ragas_scores_rejects_items_not_a_list(tmp_path):
    scores_path = tmp_path / "scores.json"
    scores_path.write_text(json.dumps({"items": "not-a-list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="list"):
        read_ragas_scores(scores_path)


@pytest.mark.parametrize("missing_field", ["collision_id", "faithfulness", "answer_relevancy"])
def test_read_ragas_scores_rejects_missing_score_field(tmp_path, missing_field):
    item = {"collision_id": "c1", "faithfulness": 0.9, "answer_relevancy": 0.85}
    del item[missing_field]
    scores_path = tmp_path / "scores.json"
    scores_path.write_text(json.dumps({"items": [item]}), encoding="utf-8")
    with pytest.raises(ValueError, match=missing_field):
        read_ragas_scores(scores_path)


def test_read_ragas_scores_rejects_item_not_an_object(tmp_path):
    scores_path = tmp_path / "scores.json"
    scores_path.write_text(json.dumps({"items": ["not-an-object"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        read_ragas_scores(scores_path)


def test_run_ragas_scoring_invokes_subprocess_by_interpreter_path_and_reads_scores_back(tmp_path):
    """Exercises run_ragas_scoring()'s cross-process mechanics -- interpreter
    invocation, argv passing, output-file read-back -- against a tiny fake
    "ragas env" script using THIS interpreter, never the real ragas_env or
    the network. Proves the subprocess wiring works without ever importing
    ragas or spending real money."""
    fake_script = tmp_path / "fake_score_with_ragas.py"
    fake_script.write_text(
        textwrap.dedent(
            """
            import json, sys
            from pathlib import Path
            input_path, output_path = Path(sys.argv[1]), Path(sys.argv[2])
            data = json.loads(input_path.read_text(encoding="utf-8"))
            items = [
                {"collision_id": it["collision_id"], "faithfulness": 0.75, "answer_relevancy": 0.65}
                for it in data["items"]
            ]
            output_path.write_text(json.dumps({"schema_version": 1, "items": items}), encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )

    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    write_ragas_input(GOOD_RECORDS, input_path)

    import evaluation.ragas_bridge as bridge
    original_script = bridge.RAGAS_SCORE_SCRIPT
    bridge.RAGAS_SCORE_SCRIPT = fake_script
    try:
        scores = run_ragas_scoring(input_path, output_path, python_path=sys.executable, timeout=30)
    finally:
        bridge.RAGAS_SCORE_SCRIPT = original_script

    assert len(scores) == 2
    assert {s["collision_id"] for s in scores} == {"c1", "c2"}
    assert all(s["faithfulness"] == pytest.approx(0.75) for s in scores)


def test_run_ragas_scoring_raises_on_nonzero_exit(tmp_path):
    fake_script = tmp_path / "fake_failing_script.py"
    fake_script.write_text("import sys; sys.exit(1)\n", encoding="utf-8")

    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    write_ragas_input(GOOD_RECORDS, input_path)

    import evaluation.ragas_bridge as bridge
    original_script = bridge.RAGAS_SCORE_SCRIPT
    bridge.RAGAS_SCORE_SCRIPT = fake_script
    try:
        with pytest.raises(RuntimeError, match="failed"):
            run_ragas_scoring(input_path, output_path, python_path=sys.executable, timeout=30)
    finally:
        bridge.RAGAS_SCORE_SCRIPT = original_script


def test_run_ragas_scoring_raises_if_interpreter_missing(tmp_path):
    input_path = tmp_path / "input.json"
    write_ragas_input(GOOD_RECORDS, input_path)
    with pytest.raises(FileNotFoundError):
        run_ragas_scoring(input_path, tmp_path / "output.json", python_path=tmp_path / "no_such_python.exe")


def test_ragas_input_record_is_a_plain_dataclass():
    # the bridge's own record type must round-trip through dataclasses.asdict
    # cleanly -- that's exactly how write_ragas_input serializes it.
    record = RagasInputRecord(collision_id="c1", question="q", answer="a", contexts=["x"])
    assert record.reference == ""
