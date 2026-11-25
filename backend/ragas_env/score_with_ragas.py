"""Standalone RAGAS scoring script. Runs under this directory's own venv
(backend/ragas_env/), NOT the main project interpreter -- it imports the
real `ragas` package, which cannot live in the main environment (see
backend/ragas_env/README.md and backend/KNOWN_ISSUES.md decision 20).

Invocation (from the main environment, via subprocess -- see
backend/evaluation/ragas_bridge.py's `run_ragas_scoring()`):

    ragas_env/Scripts/python.exe  score_with_ragas.py  <input.json>  <output.json>

`<input.json>` is whatever `ragas_bridge.write_ragas_input()` wrote:
    {"schema_version": 1, "items": [
        {"collision_id": ..., "question": ..., "answer": ..., "contexts": [...], "reference": ...},
        ...
    ]}

`<output.json>` is written here as:
    {"schema_version": 1, "items": [
        {"collision_id": ..., "faithfulness": <float|null>, "answer_relevancy": <float|null>},
        ...
    ]}

Needs a real OPENAI_API_KEY in the environment for a real run -- the judge
LLM is gpt-4.1-mini-2025-04-14 (same model used everywhere else in this
project), the embedding model is text-embedding-3-small, both called
through the real OpenAI API, never a local model. Only faithfulness and
answer_relevancy are computed (the two RAGAS metrics this project uses --
see decision 17 in KNOWN_ISSUES.md).

Uses RAGAS's legacy `ragas.metrics.faithfulness` / `answer_relevancy`
singleton metric objects (not the newer `ragas.metrics.collections.*`
classes -- those are rejected outright by `ragas.evaluate()` in this
version, 0.4.4, which only accepts the legacy `Metric` base class). The
legacy metrics are bound to a `LangchainLLMWrapper`/`LangchainEmbeddingsWrapper`
around `langchain_openai`'s `ChatOpenAI`/`OpenAIEmbeddings` -- langchain is
already a transitive dependency of `ragas` itself in this isolated venv
(see requirements.txt), so this adds no new package, and critically it
calls OpenAI's real API rather than a local sentence-transformers model,
which is the whole reason this venv exists separate from the main one.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", category=DeprecationWarning)

JUDGE_MODEL = "gpt-4.1-mini-2025-04-14"
EMBEDDING_MODEL = "text-embedding-3-small"

SCHEMA_VERSION = 1
REQUIRED_INPUT_FIELDS = ("collision_id", "question", "answer", "contexts")


def _load_input(input_path: Path) -> list[dict[str, Any]]:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError(f"{input_path}: expected a JSON object with an 'items' key")
    items = data["items"]
    if not isinstance(items, list) or not items:
        raise ValueError(f"{input_path}: 'items' must be a non-empty list")
    for i, item in enumerate(items):
        missing = [k for k in REQUIRED_INPUT_FIELDS if k not in item]
        if missing:
            raise ValueError(f"{input_path}: item {i} missing required field(s) {missing}")
        if not isinstance(item["contexts"], list) or not item["contexts"]:
            raise ValueError(f"{input_path}: item {i} 'contexts' must be a non-empty list")
    return items


def _build_llm_and_embeddings():
    """Real OpenAI-backed judge LLM and embedder. Reads OPENAI_API_KEY from
    the environment the same way the main project does -- not hardcoded,
    not read from a config file this script has no business touching."""
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings as LCOpenAIEmbeddings
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper

    chat = ChatOpenAI(model=JUDGE_MODEL, temperature=0)
    embed = LCOpenAIEmbeddings(model=EMBEDDING_MODEL)
    return LangchainLLMWrapper(chat), LangchainEmbeddingsWrapper(embed)


def score(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs ragas.evaluate() with faithfulness + answer_relevancy over
    `items` and returns one score dict per item, in input order. Callable
    directly with a fake llm/embeddings for offline validation -- see
    backend/ragas_env/validate_ragas_plumbing.py.

    Wraps the call in `get_openai_callback()` so the real run has a real
    dollar total instead of a guess -- the owner's pilot task needs exact
    spend, not an estimate. The callback only meters the chat (judge)
    calls, not the embedding calls `answer_relevancy` also makes, so the
    reported cost is a floor, not the exact total; `total_cost_usd` is put
    on every row (same total, since ragas scores the whole batch in one
    call and per-item attribution isn't available) rather than silently
    dropped."""
    from langchain_community.callbacks import get_openai_callback
    from ragas import evaluate, EvaluationDataset, SingleTurnSample
    from ragas.metrics import faithfulness, answer_relevancy

    llm, embeddings = _build_llm_and_embeddings()

    samples = [
        SingleTurnSample(
            user_input=item["question"],
            response=item["answer"],
            retrieved_contexts=item["contexts"],
            reference=item.get("reference") or None,
        )
        for item in items
    ]
    dataset = EvaluationDataset(samples=samples)

    with get_openai_callback() as cb:
        result = evaluate(
            dataset=dataset,
            metrics=[faithfulness, answer_relevancy],
            llm=llm,
            embeddings=embeddings,
            show_progress=False,
        )
        judge_cost_usd = cb.total_cost
        judge_tokens = cb.total_tokens
    rows = result.to_pandas().to_dict(orient="records")
    if len(rows) != len(items):
        raise RuntimeError(
            f"ragas.evaluate() returned {len(rows)} rows for {len(items)} input items -- "
            f"cannot align scores back to collision_ids"
        )
    return [
        {
            "collision_id": item["collision_id"],
            "faithfulness": row.get("faithfulness"),
            "answer_relevancy": row.get("answer_relevancy"),
            "judge_cost_usd_batch_total": judge_cost_usd,
            "judge_tokens_batch_total": judge_tokens,
        }
        for item, row in zip(items, rows)
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <input.json> <output.json>", file=sys.stderr)
        return 2
    input_path, output_path = Path(argv[1]), Path(argv[2])

    items = _load_input(input_path)
    scored = score(items)

    output_path.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "items": scored}, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(scored)} scored item(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
