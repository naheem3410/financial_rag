"""Pilot indexing runner — Path A over a caller-supplied doc list, with a
spend cap and per-document checkpointing so a crash resumes instead of
re-billing. Never expands doc_names itself, so scope stays caller-controlled.

Pricing table below must be kept in sync with OpenAI's pricing page by
hand — the usage object reports tokens, not dollars, so this module prices
them itself. `model=None` in estimate_cost_usd resolves to gpt-4.1-mini,
so pre-multi-model callers need no change.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from config.schema import ExperimentConfig
from llm.adapters.base import LLMCallFailed
from llm.routing import build_llm_client
from offline.corpus import parse_doc_name, resolve_pdf_paths
from offline.parsers.layout_parser import parse_document
from offline.parsers.page_filter import filter_pages
from offline.parsers.section_skip import skip_risk_factors_section
from offline.propositions.segmenter_llm import segment_pages

# $ per 1M tokens, gpt-4.1-mini-2025-04-14. Default/fallback rate below.
INPUT_PRICE_PER_1M_USD = 0.40
OUTPUT_PRICE_PER_1M_USD = 1.60

# model -> ($/1M input, $/1M output). Keyed by the pinned model string
# used in config/experiments/*.yaml.
_PRICING_PER_1M_USD: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini-2025-04-14": (INPUT_PRICE_PER_1M_USD, OUTPUT_PRICE_PER_1M_USD),
    "gpt-5.6-luna": (1.00, 6.00),
}


class SpendCapExceeded(RuntimeError):
    pass


def estimate_cost_usd(input_tokens: int, output_tokens: int, model: Optional[str] = None) -> float:
    input_price, output_price = _PRICING_PER_1M_USD.get(
        model, (INPUT_PRICE_PER_1M_USD, OUTPUT_PRICE_PER_1M_USD)
    )
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price


@dataclass(frozen=True)
class DocumentPilotResult:
    doc_name: str
    pages_total: int
    pages_skipped: dict[int, str]
    pages_segmented: int
    llm_requests: int
    input_tokens: int
    output_tokens: int
    propositions: int
    elapsed_seconds: float
    cost_usd: float


@dataclass
class PilotReport:
    documents: list[DocumentPilotResult] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return sum(d.cost_usd for d in self.documents)

    @property
    def total_requests(self) -> int:
        return sum(d.llm_requests for d in self.documents)

    @property
    def total_input_tokens(self) -> int:
        return sum(d.input_tokens for d in self.documents)

    @property
    def total_output_tokens(self) -> int:
        return sum(d.output_tokens for d in self.documents)

    @property
    def total_elapsed_seconds(self) -> float:
        return sum(d.elapsed_seconds for d in self.documents)

    @property
    def pages_skipped_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for doc in self.documents:
            for reason in doc.pages_skipped.values():
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    def extrapolate(self, full_corpus_size: int) -> dict[str, Any]:
        n = len(self.documents)
        if n == 0:
            return {}
        factor = full_corpus_size / n
        return {
            "estimated_cost_usd": round(self.total_cost_usd * factor, 4),
            "estimated_requests": round(self.total_requests * factor),
            "estimated_elapsed_seconds": round(self.total_elapsed_seconds * factor, 1),
        }


def _load_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    if checkpoint_path.exists():
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))
    return {"completed": {}}


def _save_checkpoint(checkpoint_path: Path, state: dict[str, Any]) -> None:
    checkpoint_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_pilot(
    doc_names: list[str],
    config: ExperimentConfig,
    checkpoint_path: Union[str, Path],
    max_spend_usd: float,
    document_context_by_doc: Optional[dict[str, dict[str, Any]]] = None,
    max_workers: int = 1,
    skip_risk_factors: bool = False,
) -> PilotReport:
    """Path A ingestion over doc_names only. Resumes from checkpoint_path,
    raises SpendCapExceeded before a new doc or mid-doc past max_spend_usd.

    max_workers passes through to segment_pages; usage bookkeeping is
    lock-protected so raising it is safe. skip_risk_factors defaults False —
    it's scoped to the metric-axis run, callers there pass True explicitly.
    """
    checkpoint_path = Path(checkpoint_path)
    state = _load_checkpoint(checkpoint_path)
    report = PilotReport()
    spent_so_far = sum(d["cost_usd"] for d in state["completed"].values())

    # Only resolve PDFs not already checkpointed -- a completed doc's file
    # need not even still be present to resume.
    pending = [d for d in doc_names if d not in state["completed"]]
    pdf_paths = resolve_pdf_paths(set(pending), config.corpus.pdf_dir) if pending else {}

    for doc_name in doc_names:
        if doc_name in state["completed"]:
            report.documents.append(DocumentPilotResult(**state["completed"][doc_name]))
            continue

        if spent_so_far >= max_spend_usd:
            raise SpendCapExceeded(
                f"Spend cap ${max_spend_usd:.2f} reached before starting "
                f"{doc_name!r} (${spent_so_far:.4f} spent so far)."
            )

        start = time.monotonic()
        parsed = parse_document(pdf_paths[doc_name])
        kept_pages, skipped_pages = filter_pages(parsed.pages)
        if skip_risk_factors:
            kept_pages, skipped_rf = skip_risk_factors_section(kept_pages)
            skipped_pages = {**skipped_pages, **{k: v.reason for k, v in skipped_rf.items()}}

        document_context: dict[str, Any] = {"company": doc_name, **parse_doc_name(doc_name)}
        if document_context_by_doc and doc_name in document_context_by_doc:
            document_context.update(document_context_by_doc[doc_name])

        llm_client = build_llm_client("proposition_segmenter", config)

        usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
        # max_workers>1 shares this client across threads; without the lock,
        # concurrent usage[...] += / spent_so_far += race and undercount spend.
        usage_lock = threading.Lock()
        original_complete = llm_client.complete

        def _tracked_complete(payload: dict, _client=llm_client) -> dict:
            nonlocal spent_so_far
            try:
                result = original_complete(payload)
            except LLMCallFailed as exc:
                # A failed call can still burn real tokens (length-limit
                # failures especially) -- account for them before propagating.
                exceeded = False
                if exc.partial_response is not None:
                    meta = exc.partial_response
                    with usage_lock:
                        usage["input_tokens"] += meta.input_tokens
                        usage["output_tokens"] += meta.output_tokens
                        usage["requests"] += 1
                        spent_so_far += estimate_cost_usd(meta.input_tokens, meta.output_tokens)
                        exceeded = spent_so_far >= max_spend_usd
                if exceeded:
                    raise SpendCapExceeded(
                        f"Spend cap ${max_spend_usd:.2f} reached mid-document "
                        f"{doc_name!r} (${spent_so_far:.4f} spent so far)."
                    ) from exc
                raise

            # last_response_meta is thread-local -- safe even when other
            # threads are mid-call on the same shared _client.
            meta = _client.last_response_meta
            with usage_lock:
                usage["input_tokens"] += meta.input_tokens
                usage["output_tokens"] += meta.output_tokens
                usage["requests"] += 1
                spent_so_far += estimate_cost_usd(meta.input_tokens, meta.output_tokens)
                exceeded = spent_so_far >= max_spend_usd
            if exceeded:
                raise SpendCapExceeded(
                    f"Spend cap ${max_spend_usd:.2f} reached mid-document "
                    f"{doc_name!r} (${spent_so_far:.4f} spent so far)."
                )
            return result

        llm_client.complete = _tracked_complete  # type: ignore[method-assign]

        segmented = segment_pages(kept_pages, document_context, llm_client, batch_size=2, max_workers=max_workers)

        elapsed = time.monotonic() - start
        cost = estimate_cost_usd(usage["input_tokens"], usage["output_tokens"])

        result = DocumentPilotResult(
            doc_name=doc_name,
            pages_total=len(parsed.pages),
            pages_skipped=skipped_pages,
            pages_segmented=len(kept_pages),
            llm_requests=usage["requests"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            propositions=len(segmented),
            elapsed_seconds=elapsed,
            cost_usd=cost,
        )
        report.documents.append(result)

        state["completed"][doc_name] = {
            "doc_name": result.doc_name,
            "pages_total": result.pages_total,
            "pages_skipped": result.pages_skipped,
            "pages_segmented": result.pages_segmented,
            "llm_requests": result.llm_requests,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "propositions": result.propositions,
            "elapsed_seconds": result.elapsed_seconds,
            "cost_usd": result.cost_usd,
        }
        _save_checkpoint(checkpoint_path, state)

    return report
