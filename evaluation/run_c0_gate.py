"""C0 end-to-end evaluation harness — query -> dense-only -> naive answer ->
citation over C0's separate fixed-size-chunk index, scored against the
same metric-axis gold key as C1-C4. Reuses `evaluation.run_c1_gate`'s
scoring (by-value matching, scale detection) unchanged — C0's
`AnswerValue` is the same class C1-C4 use (`online/output/naive_generator.py`
imports it from `online/output/generator.py`), so the same
`is_exact_match`/`generated_values_in_base_units` apply without
modification.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from config.schema import ExperimentConfig
from contracts.interfaces import Embedder
from llm.adapters.base import LLMCallFailed
from llm.routing import build_llm_client
from offline.pilot import SpendCapExceeded, estimate_cost_usd
from online.pipeline_c0 import C0AnswerResult, answer_question
from evaluation.run_c1_gate import gold_values_in_base_units, generated_values_in_base_units


def is_exact_match(result: C0AnswerResult, gold_answers: dict[str, Optional[float]], scale: str) -> bool:
    if result.abstained or not result.answerable:
        return False
    needed = gold_values_in_base_units(gold_answers, scale)
    if not needed:
        return False
    return needed.issubset(generated_values_in_base_units(result.answers))


@dataclass
class C0QuestionRecord:
    collision_id: str
    question: str
    verification_status: str
    exact_match: Optional[bool]
    abstained: bool
    answerable: bool
    answers: list[dict[str, str]]
    answer_text: str
    citations: list[dict[str, Any]]
    retrieved_chunk_count: int
    generator_model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    elapsed_seconds: float


def _load_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    if checkpoint_path.exists():
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))
    return {"completed": {}}


def _save_checkpoint(checkpoint_path: Path, state: dict[str, Any]) -> None:
    checkpoint_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _tracked_client(base_client, usage: dict, usage_lock: threading.Lock, spend_state: dict, max_spend_usd: float, label: str, model: str):
    original_complete = base_client.complete

    def _tracked_complete(payload: dict) -> dict:
        try:
            result = original_complete(payload)
        except LLMCallFailed as exc:
            if exc.partial_response is not None:
                meta = exc.partial_response
                call_cost = estimate_cost_usd(meta.input_tokens, meta.output_tokens, model=model)
                with usage_lock:
                    usage["input_tokens"] += meta.input_tokens
                    usage["output_tokens"] += meta.output_tokens
                    usage["requests"] += 1
                    usage["cost_usd"] += call_cost
                    spend_state["spent"] += call_cost
                    exceeded = spend_state["spent"] >= max_spend_usd
                if exceeded:
                    raise SpendCapExceeded(
                        f"Spend cap ${max_spend_usd:.2f} reached during {label} (${spend_state['spent']:.4f} spent so far)."
                    ) from exc
            raise
        meta = base_client.last_response_meta
        call_cost = estimate_cost_usd(meta.input_tokens, meta.output_tokens, model=model)
        with usage_lock:
            usage["input_tokens"] += meta.input_tokens
            usage["output_tokens"] += meta.output_tokens
            usage["requests"] += 1
            usage["cost_usd"] += call_cost
            spend_state["spent"] += call_cost
            exceeded = spend_state["spent"] >= max_spend_usd
        if exceeded:
            raise SpendCapExceeded(
                f"Spend cap ${max_spend_usd:.2f} reached during {label} (${spend_state['spent']:.4f} spent so far)."
            )
        return result

    base_client.complete = _tracked_complete
    return base_client


def run_c0_question_once(
    record: dict,
    bundle: Any,
    embedder: Embedder,
    config: ExperimentConfig,
    spend_state: dict,
    max_spend_usd: float,
) -> C0QuestionRecord:
    collision_id = record["collision_id"]
    generator_model = config.llm.call_sites["generator"].model

    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
    usage_lock = threading.Lock()
    generator_client = _tracked_client(
        build_llm_client("generator", config, schema_key="naive_generator"),
        usage, usage_lock, spend_state, max_spend_usd, collision_id, model=generator_model,
    )

    start = time.monotonic()
    result = answer_question(record["question"], bundle, embedder, generator_client, config, generator_model=generator_model)
    elapsed = time.monotonic() - start

    verification_status = record["verification_status"]
    exact_match = (
        is_exact_match(result, record["gold_answers"], record["scale"])
        if verification_status == "verified"
        else None
    )

    return C0QuestionRecord(
        collision_id=collision_id,
        question=record["question"],
        verification_status=verification_status,
        exact_match=exact_match,
        abstained=result.abstained,
        answerable=result.answerable,
        answers=[{"metric": a.metric, "value": a.value, "unit": a.unit} for a in result.answers],
        answer_text=result.answer_text,
        citations=result.citations,
        retrieved_chunk_count=result.retrieved_chunk_count,
        generator_model=result.generator_model,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cost_usd=usage["cost_usd"],
        elapsed_seconds=elapsed,
    )


def run_c0_gate(
    records: Sequence[dict],
    bundle: Any,
    embedder: Embedder,
    config: ExperimentConfig,
    checkpoint_path: Union[str, Path],
    max_spend_usd: float,
) -> list[C0QuestionRecord]:
    checkpoint_path = Path(checkpoint_path)
    state = _load_checkpoint(checkpoint_path)
    spend_state = {"spent": sum(d["cost_usd"] for d in state["completed"].values())}

    results: list[C0QuestionRecord] = []
    for record in records:
        collision_id = record["collision_id"]
        if collision_id in state["completed"]:
            results.append(C0QuestionRecord(**state["completed"][collision_id]))
            continue

        if spend_state["spent"] >= max_spend_usd:
            raise SpendCapExceeded(
                f"Spend cap ${max_spend_usd:.2f} reached before starting {collision_id!r} "
                f"(${spend_state['spent']:.4f} spent so far)."
            )

        question_record = run_c0_question_once(record, bundle, embedder, config, spend_state, max_spend_usd)
        results.append(question_record)
        state["completed"][collision_id] = question_record.__dict__
        _save_checkpoint(checkpoint_path, state)

    return results


def summarize(results: Sequence[C0QuestionRecord]) -> dict[str, Any]:
    n = len(results)
    scored = [r for r in results if r.exact_match is not None]
    negative_rejections = sum(1 for r in results if r.abstained)
    return {
        "n_questions": n,
        "n_scored_verified": len(scored),
        "n_pending_verification": n - len(scored),
        "exact_match_accuracy_verified_only": (sum(1 for r in scored if r.exact_match) / len(scored)) if scored else None,
        "negative_rejection_rate": negative_rejections / n if n else None,
        "total_cost_usd": sum(r.cost_usd for r in results),
        "total_input_tokens": sum(r.input_tokens for r in results),
        "total_output_tokens": sum(r.output_tokens for r in results),
    }
