"""Pilot indexing runner.

Runs Path A ingestion — parse PDF -> content-filter pages -> batch-segment
(`offline/propositions/segmenter_llm.py:segment_pages`) — over an explicit,
caller-supplied list of documents, with a hard spend cap checked after
every LLM call and per-document checkpointing so a mid-run failure resumes
rather than re-billing completed documents. `run_pilot` never expands
`doc_names` itself — the caller decides scope, structurally preventing an
accidental full-corpus run.

Pricing (`gpt-4.1-mini-2025-04-14`, verified against OpenAI's pricing
page): $0.40 / 1M input tokens, $0.10 / 1M cached input tokens (not
distinguished from regular input here — the adapter doesn't report cache
hits separately), $1.60 / 1M output tokens. OpenAI's usage object reports
token counts, not dollar cost; this module prices them itself from a table
that must be kept in sync with OpenAI's pricing page if a model or its
price changes.

Multi-model pricing: `gpt-5.6-luna` runs $1.00 / 1M input tokens, $6.00 /
1M output tokens — well above gpt-4.1-mini's rate, expected for a
reasoning-token model. `estimate_cost_usd`'s `model` parameter defaults to
`None`, which resolves to the gpt-4.1-mini rate — every caller that
predates multi-model pricing calls only gpt-4.1-mini and needs no change.
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

# $ per 1M tokens, gpt-4.1-mini-2025-04-14, OpenAI pricing page.
# Kept as the default/fallback rate in _PRICING_PER_1M_USD below.
INPUT_PRICE_PER_1M_USD = 0.40
OUTPUT_PRICE_PER_1M_USD = 1.60

# model -> ($/1M input tokens, $/1M output tokens). Keyed by the exact pinned
# model string used in config/experiments/*.yaml -- must be kept in sync with
# OpenAI's pricing page if a model or its price changes.
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
    """Runs Path A ingestion (parse -> filter -> batch-segment) over
    `doc_names` only. Resumes from `checkpoint_path` if it already records
    completed documents (skips re-billing them); raises `SpendCapExceeded`
    before starting a new document once `max_spend_usd` is reached, and
    mid-document after any single LLM call that would push spend past it.

    `max_workers` is passed straight through to `segment_pages` — see its
    docstring for the concurrency model. `_tracked_complete`'s
    `usage`/`spent_so_far` bookkeeping is lock-protected specifically so
    this is safe to raise above 1.

    `skip_risk_factors` applies
    `offline/parsers/section_skip.py:skip_risk_factors_section` in addition
    to the general `filter_pages`. Defaults to `False`: the Risk Factors
    skip is scoped to the metric-axis run — no question in that 37-question
    set needs the section — and must not become `run_pilot`'s default
    behavior for any other, future use of this runner. Callers running the
    metric-axis pilot pass `True` explicitly.
    """
    checkpoint_path = Path(checkpoint_path)
    state = _load_checkpoint(checkpoint_path)
    report = PilotReport()
    spent_so_far = sum(d["cost_usd"] for d in state["completed"].values())

    # Only resolve PDFs for documents not already checkpointed — a
    # completed document's file need not even still be present to resume.
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
        usage_lock = threading.Lock()  # segment_pages's max_workers>1 path can
        # call _tracked_complete from multiple threads concurrently sharing
        # this one llm_client -- without a lock, two threads' `usage[...] +=`
        # and `spent_so_far +=` read-modify-writes race and silently lose an
        # update, undercounting real spend.
        original_complete = llm_client.complete

        def _tracked_complete(payload: dict, _client=llm_client) -> dict:
            nonlocal spent_so_far
            try:
                result = original_complete(payload)
            except LLMCallFailed as exc:
                # A failed call can still have burned real tokens (a
                # length-limit failure especially) — account for them from
                # `partial_response` before letting the failure propagate to
                # segment_propositions's retry/give-up handling.
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

            # `last_response_meta` is thread-local (llm/routing.py) — safe to
            # read here even when other threads are mid-call on the same
            # shared `_client`.
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
