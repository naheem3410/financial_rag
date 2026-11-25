"""Shared gold-set loader, by `collision_id` -- used by both
`run_pilot_comparison.py` (build a setup's per-question input records) and
`metrics_aggregator.py` (look up each result's `collision_axis`).

The 366-item gold set is two files, not one: `financebench_collision_metric_axis.jsonl`
(37 items) plus `financebench_collision_extended.jsonl` (329 items).
`financebench_collision_primary.jsonl` is an older, unused draft -- never read it here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

DEFAULT_GOLD_PATHS = (
    "evaluation/data/financebench_collision_metric_axis.jsonl",
    "evaluation/data/financebench_collision_extended.jsonl",
)


def load_gold_index(paths: Union[None, list, tuple] = None) -> dict[str, dict[str, Any]]:
    """`collision_id` -> full gold record, merged across both files.
    Raises if a `collision_id` appears twice -- that would mean the two
    files overlap, which should never happen."""
    paths = paths or DEFAULT_GOLD_PATHS
    index: dict[str, dict[str, Any]] = {}
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                collision_id = record["collision_id"]
                if collision_id in index:
                    raise ValueError(f"collision_id {collision_id!r} appears in more than one gold file ({path})")
                index[collision_id] = record
    return index
