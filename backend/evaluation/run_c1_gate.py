"""C1 end-to-end harness — query -> answer -> citation over the frozen index,
scored against the metric-axis gold key.

Scoring is by value (scaled to a base unit), not by matching the model's
free-text metric label string — label phrasing varies harmlessly. Scale is
detected from the value text itself, not a schema field, since the
verbatim-guarded text always carries the scale word next to the number.
Exact-match accuracy is computed over verified rows only; unverified rows
still get a real answer but are reported separately.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from config.schema import ExperimentConfig
from contracts.interfaces import Embedder, Reranker
from contracts.numeric import normalize_value, strip_scale_word
from llm.adapters.base import LLMCallFailed
from llm.routing import build_llm_client
from offline.pilot import SpendCapExceeded, estimate_cost_usd
from online.pipeline_c1 import C1AnswerResult, answer_question

_SCALE_WORD_RE = re.compile(r"\b(thousand|million|billion)s?\b", re.IGNORECASE)
_SCALE_MULTIPLIER: dict[str, Decimal] = {
    "thousand": Decimal(1_000),
    "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
}


def detect_scale_multiplier(text: str) -> Decimal:
    match = _SCALE_WORD_RE.search(text)
    if match:
        return _SCALE_MULTIPLIER[match.group(1).lower()]
    return Decimal(1)


def _scale_key(scale: str) -> str:
    return scale.rstrip("s").lower()  # tolerate "millions" or "million"


def gold_values_in_base_units(gold_answers: dict[str, Optional[float]], scale: str) -> set[Decimal]:
    multiplier = _SCALE_MULTIPLIER.get(_scale_key(scale), Decimal(1))
    return {Decimal(str(v)) * multiplier for v in gold_answers.values() if v is not None}


def generated_values_in_base_units(answers) -> set[Decimal]:
    """Scale word usually lives in a.value, but the generator sometimes puts
    it in a.unit instead — check both or identical answers mis-score as wrong."""
    values = set()
    for a in answers:
        multiplier = detect_scale_multiplier(a.value)
        if multiplier == Decimal(1):
            multiplier = detect_scale_multiplier(a.unit)
        raw = normalize_value(strip_scale_word(a.value))
        if raw is None:
            continue
        values.add(raw * multiplier)
    return values


def is_exact_match(result: C1AnswerResult, gold_answers: dict[str, Optional[float]], scale: str) -> bool:
    if result.abstained or not result.answerable:
        return False
    needed = gold_values_in_base_units(gold_answers, scale)
    if not needed:
        return False
    return needed.issubset(generated_values_in_base_units(result.answers))


@dataclass
class C1QuestionRecord:
    collision_id: str
    question: str
    verification_status: str
    exact_match: Optional[bool]  # None for unverified rows -- not scored
    abstained: bool
    answerable: bool
    answers: list[dict[str, str]]
    answer_text: str
    citations: list[dict[str, Any]]
    fff_rewriter_model: str
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


def _tracked_client(base_client, usage: dict, usage_lock: threading.Lock, spend_state: dict, max_spend_usd: float, label: str):
    original_complete = base_client.complete

    def _tracked_complete(payload: dict) -> dict:
        try:
            result = original_complete(payload)
        except LLMCallFailed as exc:
            if exc.partial_response is not None:
                meta = exc.partial_response
                with usage_lock:
                    usage["input_tokens"] += meta.input_tokens
                    usage["output_tokens"] += meta.output_tokens
                    usage["requests"] += 1
                    spend_state["spent"] += estimate_cost_usd(meta.input_tokens, meta.output_tokens)
                    exceeded = spend_state["spent"] >= max_spend_usd
                if exceeded:
                    raise SpendCapExceeded(
                        f"Spend cap ${max_spend_usd:.2f} reached during {label} (${spend_state['spent']:.4f} spent so far)."
                    ) from exc
            raise
        meta = base_client.last_response_meta
        with usage_lock:
            usage["input_tokens"] += meta.input_tokens
            usage["output_tokens"] += meta.output_tokens
            usage["requests"] += 1
            spend_state["spent"] += estimate_cost_usd(meta.input_tokens, meta.output_tokens)
            exceeded = spend_state["spent"] >= max_spend_usd
        if exceeded:
            raise SpendCapExceeded(
                f"Spend cap ${max_spend_usd:.2f} reached during {label} (${spend_state['spent']:.4f} spent so far)."
            )
        return result

    base_client.complete = _tracked_complete
    return base_client


def run_c1_question_once(
    record: dict,
    bundle: Any,
    embedder: Embedder,
    reranker: Reranker,
    config: ExperimentConfig,
    spend_state: dict,
    max_spend_usd: float,
) -> C1QuestionRecord:
    """One query -> answer -> score pass, no checkpoint I/O (caller's job).
    Shared by run_c1_gate and self_consistency.py so both use one real path."""
    collision_id = record["collision_id"]
    fff_model = config.llm.call_sites["fff_rewriter"].model
    generator_model = config.llm.call_sites["generator"].model

    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    usage_lock = threading.Lock()
    fff_client = _tracked_client(
        build_llm_client("fff_rewriter", config), usage, usage_lock, spend_state, max_spend_usd, collision_id
    )
    generator_client = _tracked_client(
        build_llm_client("generator", config), usage, usage_lock, spend_state, max_spend_usd, collision_id
    )

    start = time.monotonic()
    result = answer_question(
        record["question"], bundle, embedder, reranker, fff_client, generator_client,
        config, fff_rewriter_model=fff_model, generator_model=generator_model,
    )
    elapsed = time.monotonic() - start

    verification_status = record["verification_status"]
    exact_match = (
        is_exact_match(result, record["gold_answers"], record["scale"])
        if verification_status == "verified"
        else None
    )

    return C1QuestionRecord(
        collision_id=collision_id,
        question=record["question"],
        verification_status=verification_status,
        exact_match=exact_match,
        abstained=result.abstained,
        answerable=result.answerable,
        answers=[{"metric": a.metric, "value": a.value, "unit": a.unit} for a in result.answers],
        answer_text=result.answer_text,
        citations=result.citations,
        fff_rewriter_model=result.fff_rewriter_model,
        generator_model=result.generator_model,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cost_usd=estimate_cost_usd(usage["input_tokens"], usage["output_tokens"]),
        elapsed_seconds=elapsed,
    )


def run_c1_gate(
    records: Sequence[dict],
    bundle: Any,
    embedder: Embedder,
    reranker: Reranker,
    config: ExperimentConfig,
    checkpoint_path: Union[str, Path],
    max_spend_usd: float,
) -> list[C1QuestionRecord]:
    checkpoint_path = Path(checkpoint_path)
    state = _load_checkpoint(checkpoint_path)
    spend_state = {"spent": sum(d["cost_usd"] for d in state["completed"].values())}

    results: list[C1QuestionRecord] = []
    for record in records:
        collision_id = record["collision_id"]
        if collision_id in state["completed"]:
            results.append(C1QuestionRecord(**state["completed"][collision_id]))
            continue

        if spend_state["spent"] >= max_spend_usd:
            raise SpendCapExceeded(
                f"Spend cap ${max_spend_usd:.2f} reached before starting {collision_id!r} "
                f"(${spend_state['spent']:.4f} spent so far)."
            )

        question_record = run_c1_question_once(record, bundle, embedder, reranker, config, spend_state, max_spend_usd)
        results.append(question_record)
        state["completed"][collision_id] = question_record.__dict__
        _save_checkpoint(checkpoint_path, state)

    return results


def summarize(results: Sequence[C1QuestionRecord]) -> dict[str, Any]:
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
