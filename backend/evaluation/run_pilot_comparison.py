"""CLI runner for the three-way comparison (KNOWN_ISSUES.md Phase 2 to-do:
"Run the three way comparison: Extended System, plain LLM, naive RAG").

One shared runner, not three thin scripts: all three setups need the exact
same three steps -- load a list of `collision_id`s, look each one up in the
366-item gold set, call that setup's existing per-question gate function --
and only differ in which infra object they need built (plain AI needs
nothing but a config; C0 needs its own index; C4+ needs the shared v2 index,
a reranker and the entity registry). Splitting that into three files would
copy the item-loading/gold-lookup/checkpoint-dispatch logic three times for
no benefit, since none of `run_plain_ai_gate.py` / `run_c0_gate.py` /
`run_c4_gate.py` themselves need touching -- this file only ever calls them.

Each setup's real infra is built by its own `build_*_context()` function,
kept separate from `run_setup()`'s dispatch logic on purpose -- a test can
monkeypatch just the context builder (swap in a fake bundle/embedder/client)
without touching the dispatch or checkpoint code being tested.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence, Union

from config.schema import load_config
from evaluation.gold_lookup import load_gold_index
from evaluation.run_c0_gate import run_c0_gate
from evaluation.run_c4_gate import run_c4_gate
from evaluation.run_plain_ai_gate import run_plain_ai_gate

REPO_BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_ITEMS_PATH = REPO_BACKEND / "evaluation" / "data" / "pilot_20.json"

PLAIN_AI_CONFIG_PATH = REPO_BACKEND / "config" / "baselines" / "plain_ai_baseline.yaml"
C0_CONFIG_PATH = REPO_BACKEND / "config" / "experiments" / "c0_naive.yaml"
C4_PLUS_CONFIG_PATH = REPO_BACKEND / "config" / "comparison" / "c4_plus.yaml"

C0_DENSE_INDEX_PATH = REPO_BACKEND / "data" / "index" / "dense_index_c0"
C0_POSTGRES_DSN = "postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_c0"

V2_DENSE_INDEX_PATH = REPO_BACKEND / "data" / "index" / "dense_index_v2"
V2_POSTGRES_DSN = "postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_v2"


# --- items / gold-record wiring ---


def load_item_ids(items_path: Union[str, Path]) -> list[str]:
    """Accepts either `pilot_20.json`'s shape (`{"items": [{"collision_id": ...}, ...]}`)
    or a plain JSON list of `collision_id` strings -- the second form exists
    so a test (or a smaller hand-picked list) doesn't need pilot_20.json's full shape."""
    data = json.loads(Path(items_path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "items" in data:
        return [item["collision_id"] for item in data["items"]]
    if isinstance(data, list):
        return list(data)
    raise ValueError(f"{items_path}: expected a pilot_20.json-shaped dict or a plain list of collision_ids")


def build_records(collision_ids: Sequence[str], gold_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per id, carrying exactly the keys the three gate
    scripts' per-question functions read off a record (`collision_id`,
    `question`, `verification_status`, `gold_answers`, `scale`) plus
    `collision_axis` kept along for the metrics aggregator's later use."""
    records = []
    for collision_id in collision_ids:
        if collision_id not in gold_index:
            raise KeyError(f"collision_id {collision_id!r} (from the items file) not found in the gold set")
        gold = gold_index[collision_id]
        records.append(
            {
                "collision_id": collision_id,
                "question": gold["question"],
                "verification_status": gold["verification_status"],
                "gold_answers": gold["gold_answers"],
                "scale": gold["scale"],
                "collision_axis": gold["collision_axis"],
            }
        )
    return records


# --- per-setup infra (each builds only what that setup's gate needs) ---


def build_plain_ai_context() -> dict[str, Any]:
    return {"config": load_config(str(PLAIN_AI_CONFIG_PATH))}


def build_c0_context() -> dict[str, Any]:
    from offline.embedding.bge_embedder import BGEEmbedder
    from offline.indexing.build import load_index

    config = load_config(str(C0_CONFIG_PATH))
    embedder = BGEEmbedder()
    bundle = load_index(C0_DENSE_INDEX_PATH, C0_POSTGRES_DSN, dimension=embedder.dimension)
    return {"config": config, "embedder": embedder, "bundle": bundle}


def build_c4_plus_context() -> dict[str, Any]:
    from infra.connections import connect_postgres
    from offline.embedding.bge_embedder import BGEEmbedder
    from offline.indexing.build import load_index
    from offline.registry.entities import build_registry, list_entities
    from online.retrieval.reranker import CrossEncoderReranker

    config = load_config(str(C4_PLUS_CONFIG_PATH))
    embedder = BGEEmbedder()
    bundle = load_index(V2_DENSE_INDEX_PATH, V2_POSTGRES_DSN, dimension=embedder.dimension)
    reranker = CrossEncoderReranker.from_config(config.retrieval)
    conn = connect_postgres(V2_POSTGRES_DSN, autocommit=True)
    registry = build_registry(list_entities(conn))
    return {"config": config, "embedder": embedder, "bundle": bundle, "reranker": reranker, "registry": registry}


SETUPS = ("plain_ai", "c0", "c4_plus")


def run_setup(
    setup: str,
    records: Sequence[dict[str, Any]],
    checkpoint_path: Union[str, Path],
    max_spend_usd: float,
) -> list[Any]:
    if setup == "plain_ai":
        ctx = build_plain_ai_context()
        return run_plain_ai_gate(records, ctx["config"], checkpoint_path, max_spend_usd)
    if setup == "c0":
        ctx = build_c0_context()
        return run_c0_gate(records, ctx["bundle"], ctx["embedder"], ctx["config"], checkpoint_path, max_spend_usd)
    if setup == "c4_plus":
        ctx = build_c4_plus_context()
        return run_c4_gate(
            records, ctx["bundle"], ctx["embedder"], ctx["reranker"], ctx["config"], ctx["registry"],
            checkpoint_path, max_spend_usd,
        )
    raise ValueError(f"unknown --setup {setup!r}, must be one of {SETUPS}")


def main(argv: Sequence[str] = None) -> None:
    parser = argparse.ArgumentParser(description="Run the pilot three-way comparison for one setup.")
    parser.add_argument("--setup", required=True, choices=SETUPS)
    parser.add_argument("--items", default=str(DEFAULT_ITEMS_PATH), help="Path to a pilot_20.json-shaped item list")
    parser.add_argument("--checkpoint", required=True, help="Checkpoint JSON path for this setup's run")
    parser.add_argument("--max-spend-usd", type=float, default=5.0)
    args = parser.parse_args(argv)

    collision_ids = load_item_ids(args.items)
    gold_index = load_gold_index()
    records = build_records(collision_ids, gold_index)

    results = run_setup(args.setup, records, args.checkpoint, args.max_spend_usd)
    print(f"[run_pilot_comparison] setup={args.setup} n_items={len(records)} n_results={len(results)}")


if __name__ == "__main__":
    main()
