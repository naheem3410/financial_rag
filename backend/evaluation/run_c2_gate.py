"""C2 end-to-end harness — query -> answer -> CoVe-verified -> citation.
Mirrors run_c1_gate for retrieval/scoring; only difference is three extra
tracked CoVe clients, each billed at cove_verification's own model price.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from config.schema import ExperimentConfig
from contracts.interfaces import Embedder, Reranker
from llm.adapters.base import LLMCallFailed
from llm.routing import build_llm_client
from offline.pilot import SpendCapExceeded, estimate_cost_usd
from online.pipeline_c2 import C2AnswerResult, answer_question
from evaluation.run_c1_gate import gold_values_in_base_units, generated_values_in_base_units


def is_exact_match(result: C2AnswerResult, gold_answers: dict[str, Optional[float]], scale: str) -> bool:
    if result.abstained or not result.answerable:
        return False
    needed = gold_values_in_base_units(gold_answers, scale)
    if not needed:
        return False
    return needed.issubset(generated_values_in_base_units(result.answers))


@dataclass
class C2QuestionRecord:
    collision_id: str
    question: str
    verification_status: str
    exact_match: Optional[bool]  # None for unverified rows -- not scored
    abstained: bool
    answerable: bool
    answers: list[dict[str, str]]
    answer_text: str
    citations: list[dict[str, Any]]
    verification_trace: list[dict[str, Any]]
    fff_rewriter_model: str
    generator_model: str
    cove_verification_model: str
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


def _tracked_client(
    base_client,
    usage: dict,
    usage_lock: threading.Lock,
    spend_state: dict,
    max_spend_usd: float,
    label: str,
    model: str,
):
    """Costs accumulate per-call at that call's own model price, unlike C1's
    version — C2 mixes FFF/Generator and CoVe models at different rates."""
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


def run_c2_question_once(
    record: dict,
    bundle: Any,
    embedder: Embedder,
    reranker: Reranker,
    config: ExperimentConfig,
    spend_state: dict,
    max_spend_usd: float,
) -> C2QuestionRecord:
    """One query -> answer -> CoVe-verified -> score pass, no checkpoint I/O
    (caller's job — see run_c1_gate's equivalent for why it's split out)."""
    collision_id = record["collision_id"]
    fff_model = config.llm.call_sites["fff_rewriter"].model
    generator_model = config.llm.call_sites["generator"].model
    cove_model = config.llm.call_sites["cove_verification"].model

    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
    usage_lock = threading.Lock()
    fff_client = _tracked_client(
        build_llm_client("fff_rewriter", config), usage, usage_lock, spend_state, max_spend_usd,
        collision_id, model=fff_model,
    )
    generator_client = _tracked_client(
        build_llm_client("generator", config), usage, usage_lock, spend_state, max_spend_usd,
        collision_id, model=generator_model,
    )
    claim_extraction_client = _tracked_client(
        build_llm_client("cove_verification", config, schema_key="cove_claim_extraction"),
        usage, usage_lock, spend_state, max_spend_usd, collision_id, model=cove_model,
    )
    independent_verification_client = _tracked_client(
        build_llm_client("cove_verification", config, schema_key="cove_independent_verification"),
        usage, usage_lock, spend_state, max_spend_usd, collision_id, model=cove_model,
    )
    prose_comparison_client = _tracked_client(
        build_llm_client("cove_verification", config, schema_key="cove_prose_comparison"),
        usage, usage_lock, spend_state, max_spend_usd, collision_id, model=cove_model,
    )

    start = time.monotonic()
    result = answer_question(
        record["question"], bundle, embedder, reranker,
        fff_client, generator_client,
        claim_extraction_client, independent_verification_client, prose_comparison_client,
        config, fff_rewriter_model=fff_model, generator_model=generator_model, cove_verification_model=cove_model,
    )
    elapsed = time.monotonic() - start

    verification_status = record["verification_status"]
    exact_match = (
        is_exact_match(result, record["gold_answers"], record["scale"])
        if verification_status == "verified"
        else None
    )

    return C2QuestionRecord(
        collision_id=collision_id,
        question=record["question"],
        verification_status=verification_status,
        exact_match=exact_match,
        abstained=result.abstained,
        answerable=result.answerable,
        answers=[{"metric": a.metric, "value": a.value, "unit": a.unit} for a in result.answers],
        answer_text=result.answer_text,
        citations=result.citations,
        verification_trace=result.verification_trace,
        fff_rewriter_model=result.fff_rewriter_model,
        generator_model=result.generator_model,
        cove_verification_model=result.cove_verification_model,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cost_usd=usage["cost_usd"],
        elapsed_seconds=elapsed,
    )


def run_c2_gate(
    records: Sequence[dict],
    bundle: Any,
    embedder: Embedder,
    reranker: Reranker,
    config: ExperimentConfig,
    checkpoint_path: Union[str, Path],
    max_spend_usd: float,
) -> list[C2QuestionRecord]:
    checkpoint_path = Path(checkpoint_path)
    state = _load_checkpoint(checkpoint_path)
    spend_state = {"spent": sum(d["cost_usd"] for d in state["completed"].values())}

    results: list[C2QuestionRecord] = []
    for record in records:
        collision_id = record["collision_id"]
        if collision_id in state["completed"]:
            results.append(C2QuestionRecord(**state["completed"][collision_id]))
            continue

        if spend_state["spent"] >= max_spend_usd:
            raise SpendCapExceeded(
                f"Spend cap ${max_spend_usd:.2f} reached before starting {collision_id!r} "
                f"(${spend_state['spent']:.4f} spent so far)."
            )

        question_record = run_c2_question_once(record, bundle, embedder, reranker, config, spend_state, max_spend_usd)
        results.append(question_record)
        state["completed"][collision_id] = question_record.__dict__
        _save_checkpoint(checkpoint_path, state)

    return results


def summarize(results: Sequence[C2QuestionRecord]) -> dict[str, Any]:
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
