"""Data interchange with the isolated RAGAS-scoring environment.

Why this exists: `ragas` cannot be installed into this project's main
environment -- its `datasets` dependency drags `huggingface-hub` to a
version `transformers`/`sentence-transformers` won't accept (KNOWN_ISSUES.md
decision 20). So RAGAS scoring runs in a separate venv (`backend/ragas_env/`,
see `backend/ragas_env/README.md`) under its own Python interpreter, and the
two sides talk only through plain JSON files on disk -- never a shared
import. This module is the main-environment half of that bridge: writing
the input file the isolated script reads, running it as a subprocess, and
reading the scores file it writes back. It never imports `ragas` itself.

Invocation: `run_ragas_scoring()` shells out to the isolated venv's own
`python.exe` by its literal path, passing the input and output JSON paths
as positional arguments -- the same interpreter-by-path approach works
unchanged on any OS once that path is right for the platform.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence, Union

SCHEMA_VERSION = 1

REPO_BACKEND = Path(__file__).resolve().parents[1]
RAGAS_ENV_DIR = REPO_BACKEND / "ragas_env"
RAGAS_SCORE_SCRIPT = RAGAS_ENV_DIR / "score_with_ragas.py"


def _ragas_env_python() -> Path:
    """The isolated venv's own interpreter, picked by platform layout --
    Windows venvs put it under Scripts/, POSIX under bin/."""
    if sys.platform.startswith("win"):
        return RAGAS_ENV_DIR / "Scripts" / "python.exe"
    return RAGAS_ENV_DIR / "bin" / "python"


RAGAS_ENV_PYTHON = _ragas_env_python()


@dataclass(frozen=True)
class RagasInputRecord:
    """One question's worth of data for RAGAS's `evaluate()`. `reference`
    is the gold answer text; left as "" when there isn't one, since
    `answer_relevancy` doesn't need it (only some other RAGAS metrics do)."""

    collision_id: str
    question: str
    answer: str
    contexts: list[str]
    reference: str = ""


def _require_str(record: Mapping[str, Any], key: str, index: int) -> str:
    if key not in record:
        raise ValueError(f"record {index}: missing required field {key!r}")
    value = record[key]
    if not isinstance(value, str):
        raise ValueError(f"record {index}: field {key!r} must be a string, got {type(value).__name__}")
    return value


def _validate_record(record: Mapping[str, Any], index: int) -> RagasInputRecord:
    collision_id = _require_str(record, "collision_id", index)
    question = _require_str(record, "question", index)
    answer = _require_str(record, "answer", index)
    if "contexts" not in record:
        raise ValueError(f"record {index}: missing required field 'contexts'")
    contexts = record["contexts"]
    if not isinstance(contexts, list) or not all(isinstance(c, str) for c in contexts):
        raise ValueError(f"record {index}: 'contexts' must be a list of strings")
    if not contexts:
        raise ValueError(f"record {index}: 'contexts' must not be empty -- RAGAS needs at least one retrieved context")
    reference = record.get("reference", "")
    if not isinstance(reference, str):
        raise ValueError(f"record {index}: 'reference' must be a string")
    return RagasInputRecord(
        collision_id=collision_id,
        question=question,
        answer=answer,
        contexts=list(contexts),
        reference=reference,
    )


def write_ragas_input(records: Sequence[Mapping[str, Any]], output_path: Union[str, Path]) -> None:
    """Validates every record and writes the isolated script's input file.
    Raises `ValueError` on the first malformed record rather than writing
    a partial or silently-wrong file."""
    if not records:
        raise ValueError("records must not be empty")
    validated = [_validate_record(record, i) for i, record in enumerate(records)]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "items": [asdict(r) for r in validated],
    }
    Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


REQUIRED_SCORE_FIELDS = ("collision_id", "faithfulness", "answer_relevancy")


def read_ragas_scores(scores_path: Union[str, Path]) -> list[dict[str, Any]]:
    """Reads back the isolated script's output file. Raises loudly if the
    shape isn't what `score_with_ragas.py` is supposed to produce -- a
    silently-missing score field is worse than a crash here."""
    raw = Path(scores_path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError(f"{scores_path}: expected a JSON object with an 'items' key")
    items = data["items"]
    if not isinstance(items, list):
        raise ValueError(f"{scores_path}: 'items' must be a list")
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{scores_path}: item {i} must be an object")
        for key in REQUIRED_SCORE_FIELDS:
            if key not in item:
                raise ValueError(f"{scores_path}: item {i} missing required field {key!r}")
    return items


def run_ragas_scoring(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    *,
    python_path: Union[str, Path, None] = None,
    timeout: float = 1800.0,
) -> list[dict[str, Any]]:
    """Shells out to the isolated venv's Python, running `score_with_ragas.py
    <input_path> <output_path>`, then reads and validates the scores file it
    produced. `python_path` is overridable for tests; defaults to the real
    venv interpreter next to this file's sibling `ragas_env/` directory.
    Never called by the main pytest suite -- it needs the real ragas_env and
    (for real scores) a live OPENAI_API_KEY, both out of scope for `pytest`."""
    interpreter = Path(python_path) if python_path is not None else RAGAS_ENV_PYTHON
    if not interpreter.exists():
        raise FileNotFoundError(
            f"ragas_env interpreter not found at {interpreter} -- build it first "
            f"(see backend/ragas_env/README.md)"
        )
    result = subprocess.run(
        [str(interpreter), str(RAGAS_SCORE_SCRIPT), str(input_path), str(output_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ragas scoring subprocess failed (exit {result.returncode}).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return read_ragas_scores(output_path)
