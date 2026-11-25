"""Plain AI baseline harness (step 5.1, decision 1): runs every question
straight at the model with no retrieval, the floor every other setup
(C0, C4+) is measured against.

The checkpoint/cost-tracking helpers below are deliberately a local copy,
not a cross-module import of run_c0_gate.py's private ones -- every other
gate file (run_c1/c2/c3/c4_gate.py) already keeps its own copy rather than
reaching into another gate file's underscored names, and this follows that
same established convention.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from config.schema import ExperimentConfig
from evaluation.run_c1_gate import gold_values_in_base_units, generated_values_in_base_units
from llm.adapters.base import LLMCallFailed
from llm.routing import build_llm_client
from offline.pilot import SpendCapExceeded, estimate_cost_usd
from online.pipeline_plain_ai import PlainAIAnswerResult, answer_question


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


def is_exact_match(result: PlainAIAnswerResult, gold_answers: dict[str, Optional[float]], scale: str) -> bool:
    if result.abstained or not result.answerable:
        return False
    needed = gold_values_in_base_units(gold_answers, scale)
    if not needed:
        return False
    return needed.issubset(generated_values_in_base_units(result.answers))


@dataclass
class PlainAIQuestionRecord:
    collision_id: str
    question: str
    verification_status: str
    exact_match: Optional[bool]
    abstained: bool
    answerable: bool
    answers: list[dict[str, str]]
    answer_text: str
    # Always empty/zero -- this setup has no retrieval or citations at
    # all. Kept as fields (not omitted) so a results-table builder
    # expecting the same shape as C0-C4's records doesn't need a special
    # case for this one setup.
    citations: list[dict[str, Any]]
    retrieved_chunk_count: int
    generator_model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    elapsed_seconds: float


def run_plain_ai_question_once(
    record: dict,
    config: ExperimentConfig,
    spend_state: dict,
    max_spend_usd: float,
) -> PlainAIQuestionRecord:
    collision_id = record["collision_id"]
    generator_model = config.llm.call_sites["plain_ai_baseline"].model

    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
    usage_lock = threading.Lock()
    client = _tracked_client(
        build_llm_client("plain_ai_baseline", config),
        usage, usage_lock, spend_state, max_spend_usd, collision_id, model=generator_model,
    )

    start = time.monotonic()
    result = answer_question(record["question"], client, generator_model=generator_model)
    elapsed = time.monotonic() - start

    verification_status = record["verification_status"]
    exact_match = (
        is_exact_match(result, record["gold_answers"], record["scale"])
        if verification_status == "verified"
        else None
    )

    return PlainAIQuestionRecord(
        collision_id=collision_id,
        question=record["question"],
        verification_status=verification_status,
        exact_match=exact_match,
        abstained=result.abstained,
        answerable=result.answerable,
        answers=[{"metric": a.metric, "value": a.value, "unit": a.unit} for a in result.answers],
        answer_text=result.answer_text,
        citations=[],
        retrieved_chunk_count=0,
        generator_model=result.generator_model,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cost_usd=usage["cost_usd"],
        elapsed_seconds=elapsed,
    )


def run_plain_ai_gate(
    records: Sequence[dict],
    config: ExperimentConfig,
    checkpoint_path: Union[str, Path],
    max_spend_usd: float,
) -> list[PlainAIQuestionRecord]:
    checkpoint_path = Path(checkpoint_path)
    state = _load_checkpoint(checkpoint_path)
    spend_state = {"spent": sum(d["cost_usd"] for d in state["completed"].values())}

    results: list[PlainAIQuestionRecord] = []
    for record in records:
        collision_id = record["collision_id"]
        if collision_id in state["completed"]:
            results.append(PlainAIQuestionRecord(**state["completed"][collision_id]))
            continue

        if spend_state["spent"] >= max_spend_usd:
            raise SpendCapExceeded(
                f"Spend cap ${max_spend_usd:.2f} reached before starting {collision_id!r} "
                f"(${spend_state['spent']:.4f} spent so far)."
            )

        question_record = run_plain_ai_question_once(record, config, spend_state, max_spend_usd)
        results.append(question_record)
        state["completed"][collision_id] = question_record.__dict__
        _save_checkpoint(checkpoint_path, state)

    return results


def summarize(results: Sequence[PlainAIQuestionRecord]) -> dict[str, Any]:
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
