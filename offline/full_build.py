"""Full production build orchestration — parse -> filter -> segment ->
numeric-extract -> metadata-tag -> persist.

`offline/pilot.py:run_pilot` deliberately stops at segmentation — it exists
to measure cost/timing/content-filter behavior cheaply, not to produce a
usable index (its `DocumentPilotResult` records only a proposition count).
This module is the real thing: it carries the segmented text all the way
through `Proposition` assembly and into `offline.indexing.build.build_index`,
so the result is the actual three-index build C0-C4 reuse unchanged — not a
second, throwaway pass.

Reuses the same building blocks `run_pilot` exercises (page_filter,
section_skip, segment_pages, the LLM routing/rate-limiter/cost-tracking
machinery) rather than re-implementing them, and keeps the same safety
posture: hard spend cap checked after every LLM call, per-document
checkpointing so a crash mid-run resumes without re-billing completed
documents, and `LLMInfrastructureError` propagates uncaught (never
degraded to "this document produced nothing") — see
`llm/adapters/base.py:LLMInfrastructureError`.

Checkpointing persists propositions, not just counts: a resumed run must
not re-invoke the LLM segmenter for an already-completed document, so each
completed document's fully-assembled `Proposition` list is written to a
JSON sidecar file (`<checkpoint_path>.<doc_name>.propositions.json`)
alongside the main checkpoint's completion record. Resuming loads
propositions back from that sidecar rather than re-deriving them.

Per-batch checkpointing within a document: the above is per-document, so a
crash partway through a long document's segmentation would otherwise lose
all of that document's progress. A second sidecar
(`<checkpoint_path>.<doc_name>.batches.json`) records each batch's result
as soon as it completes (`segment_pages`'s `on_batch_complete` hook) and is
loaded back on resume (`completed_batches`) so only the batch that was
genuinely in flight — not the whole document — is re-sent to the LLM.
`on_batch_start` logs each batch before its LLM call, so the last logged
line identifies the batch in flight if a run crashes. Deleted once the
document completes; it exists only to survive a mid-document crash.

TPM observability: a background thread polls
`RoutedLLMClient.current_tpm_usage()` (llm/rate_limiter.py's existing
sliding-window counter, not a new tracking mechanism) once a minute and
logs it, so a real run's throughput is visible rather than inferred from
the post-hoc average.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Optional, Union

from config.schema import ExperimentConfig
from contracts.interfaces import Embedder
from contracts.models import Confidence, Proposition, PropositionMetadata
from llm.adapters.base import LLMCallFailed
from llm.routing import LLMCallSiteManifest, build_llm_client
from offline.corpus import parse_doc_name
from offline.extraction.numeric_extractor import extract as extract_numeric
from offline.gold_overlap import GoldPageOverlap, find_gold_page_overlaps
from offline.indexing.build import build_index
from offline.manifest import RunManifest
from offline.metadata.tagger import tag as tag_metadata
from offline.parsers.filing_date import extract_filing_period_end
from offline.parsers.layout_parser import ParsedPage, parse_document
from offline.parsers.page_filter import filter_pages
from offline.parsers.section_skip import skip_risk_factors_section
from offline.pilot import SpendCapExceeded, estimate_cost_usd
from offline.propositions.segmenter_llm import SegmentedProposition, segment_pages


def _proposition_id(source_uri: str, page: int, text: str, occurrence: int) -> str:
    """Deterministic and stable given the same input — no randomness, no
    incrementing global counter. `occurrence` disambiguates the rare case
    of identical (source_uri, page, text) appearing more than once (mirrors
    offline/propositions/row_templating.py's Path B scheme)."""
    key = f"{source_uri}|{page}|{text}|{occurrence}"
    return "p_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _log(message: str) -> None:
    print(f"[full_build {time.strftime('%H:%M:%S')}] {message}", flush=True)


def _document_context_for(doc_name: str, pages: list[ParsedPage], company_names: dict[str, str]) -> dict[str, Any]:
    parsed_fields = parse_doc_name(doc_name)
    filing_date = extract_filing_period_end(pages)
    if filing_date is None:
        raise ValueError(
            f"full_build: could not find a 'For the [fiscal year|quarterly period] ended <date>' "
            f"statement on any page of {doc_name!r} — refusing to fabricate a filing_date."
        )
    return {
        "company": company_names[doc_name],
        "filing_type": parsed_fields["filing_type"],
        "filing_date": filing_date,
        "source_uri": doc_name,
        "fiscal_year": parsed_fields.get("fiscal_year"),
        "quarter": parsed_fields.get("quarter"),
    }


def _assemble_propositions(
    segmented: list[SegmentedProposition],
    document_context: dict[str, Any],
    llm_client: Any,
) -> list[Proposition]:
    occurrence_counts: dict[tuple[str, int, str], int] = {}
    source_uri = document_context["source_uri"]
    propositions: list[Proposition] = []
    for seg in segmented:
        page = seg.page_number if seg.page_number is not None else 0
        key = (source_uri, page, seg.text)
        occurrence = occurrence_counts.get(key, 0)
        occurrence_counts[key] = occurrence + 1

        extraction = extract_numeric(seg.text, document_context, llm_client)
        metadata: PropositionMetadata = tag_metadata(document_context, section="", page=page)
        propositions.append(
            Proposition(
                proposition_id=_proposition_id(source_uri, page, seg.text, occurrence),
                text=seg.text,
                provenance_tier="retrieved",
                ingestion_path="A_unstructured",
                numeric=extraction.numeric,
                confidence=Confidence(extraction_tier=extraction.extraction_tier or "rules"),
                metadata=metadata,
            )
        )
    return propositions


def _load_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    if checkpoint_path.exists():
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))
    return {"completed": {}}


def _save_checkpoint(checkpoint_path: Path, state: dict[str, Any]) -> None:
    checkpoint_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _propositions_sidecar_path(checkpoint_path: Path, doc_name: str) -> Path:
    return checkpoint_path.with_name(f"{checkpoint_path.stem}.{doc_name}.propositions.json")


def _batch_sidecar_path(checkpoint_path: Path, doc_name: str) -> Path:
    """Per-batch progress within a single document's segmentation —
    distinct from `_propositions_sidecar_path`, which only exists once a
    whole document completes. Deleted once the document finishes; its only
    purpose is surviving a crash mid-document."""
    return checkpoint_path.with_name(f"{checkpoint_path.stem}.{doc_name}.batches.json")


def _load_completed_batches(checkpoint_path: Path, doc_name: str) -> dict[int, list[SegmentedProposition]]:
    path = _batch_sidecar_path(checkpoint_path, doc_name)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(batch_index): [SegmentedProposition(**prop) for prop in props]
        for batch_index, props in raw.items()
    }


def _save_completed_batches(checkpoint_path: Path, doc_name: str, batches: dict[int, list[SegmentedProposition]]) -> None:
    path = _batch_sidecar_path(checkpoint_path, doc_name)
    serializable = {
        str(batch_index): [asdict(prop) for prop in props]
        for batch_index, props in batches.items()
    }
    path.write_text(json.dumps(serializable), encoding="utf-8")


def _clear_batch_sidecar(checkpoint_path: Path, doc_name: str) -> None:
    path = _batch_sidecar_path(checkpoint_path, doc_name)
    if path.exists():
        path.unlink()


def _tracked_client(
    base_client: Any,
    model: Optional[str],
    usage: dict,
    usage_lock: threading.Lock,
    spend_state: dict,
    max_spend_usd: float,
    doc_name: str,
) -> Any:
    """Wraps `base_client.complete` to accumulate token usage/cost into the
    shared `usage`/`spend_state`, priced at `model`'s own rate
    (`offline/pilot.py:estimate_cost_usd`'s `model` parameter). This matters
    because the escalation path (below) uses a second client priced
    differently from the primary model, sharing this document's spend
    tracking -- pricing every call at a single default rate would silently
    misprice escalation calls."""
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
                        f"Spend cap ${max_spend_usd:.2f} reached mid-document {doc_name!r} "
                        f"(${spend_state['spent']:.4f} spent so far)."
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
                f"Spend cap ${max_spend_usd:.2f} reached mid-document {doc_name!r} "
                f"(${spend_state['spent']:.4f} spent so far)."
            )
        return result

    base_client.complete = _tracked_complete
    return base_client


def _start_tpm_logger(llm_client: Any, stop_event: threading.Event, interval_seconds: float = 60.0) -> threading.Thread:
    def _run() -> None:
        while not stop_event.wait(interval_seconds):
            usage = llm_client.current_tpm_usage()
            if usage is not None:
                _log(f"TPM usage (60s sliding window): {usage:,}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def run_full_build(
    doc_names: list[str],
    config: ExperimentConfig,
    pdf_paths: dict[str, Path],
    company_names: dict[str, str],
    checkpoint_path: Union[str, Path],
    max_spend_usd: float,
    embedder: Embedder,
    dense_index_path: Union[str, Path],
    postgres_dsn: str,
    config_hash: str = "",
    max_workers: int = 1,
    skip_risk_factors: bool = False,
    gold_pages_by_doc: Optional[dict[str, set[int]]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> RunManifest:
    """Builds the real, persisted three-index production build over
    `doc_names` — see module docstring. Returns the `RunManifest` C0-C4
    should all reuse unchanged; does not call `build_index` more than once
    across the whole run.
    """
    checkpoint_path = Path(checkpoint_path)
    state = _load_checkpoint(checkpoint_path)
    spend_state = {"spent": sum(d["cost_usd"] for d in state["completed"].values())}

    all_propositions: list[Proposition] = []
    skipped_pages_by_doc: dict[str, dict[int, str]] = {}
    total_requests = total_input_tokens = total_output_tokens = 0
    total_cost_usd = 0.0
    total_elapsed = 0.0
    call_site_manifest: Optional[LLMCallSiteManifest] = (
        LLMCallSiteManifest(**state["call_site_manifest"]) if "call_site_manifest" in state else None
    )

    for doc_name in doc_names:
        if doc_name in state["completed"]:
            sidecar = _propositions_sidecar_path(checkpoint_path, doc_name)
            props = [Proposition.model_validate(p) for p in json.loads(sidecar.read_text(encoding="utf-8"))]
            all_propositions.extend(props)
            skipped_pages_by_doc[doc_name] = state["completed"][doc_name]["pages_skipped"]
            total_requests += state["completed"][doc_name]["llm_requests"]
            total_input_tokens += state["completed"][doc_name]["input_tokens"]
            total_output_tokens += state["completed"][doc_name]["output_tokens"]
            total_cost_usd += state["completed"][doc_name]["cost_usd"]
            total_elapsed += state["completed"][doc_name]["elapsed_seconds"]
            if progress_callback:
                progress_callback(f"{doc_name}: resumed from checkpoint ({len(props)} propositions)")
            continue

        if spend_state["spent"] >= max_spend_usd:
            raise SpendCapExceeded(
                f"Spend cap ${max_spend_usd:.2f} reached before starting {doc_name!r} "
                f"(${spend_state['spent']:.4f} spent so far)."
            )

        _log(f"starting {doc_name}")
        start = time.monotonic()
        parsed = parse_document(pdf_paths[doc_name])
        kept_pages, skipped_pages = filter_pages(parsed.pages)
        if skip_risk_factors:
            kept_pages, skipped_rf = skip_risk_factors_section(kept_pages)
            skipped_pages = {**skipped_pages, **{k: v.reason for k, v in skipped_rf.items()}}
        skipped_pages_by_doc[doc_name] = skipped_pages

        document_context = _document_context_for(doc_name, parsed.pages, company_names)
        segmenter_model = config.llm.call_sites["proposition_segmenter"].model
        raw_llm_client = build_llm_client("proposition_segmenter", config)

        usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
        usage_lock = threading.Lock()
        llm_client = _tracked_client(
            raw_llm_client, segmenter_model, usage, usage_lock, spend_state, max_spend_usd, doc_name
        )

        # Escalation: the primary model can unreliably separate a table
        # row's real value from a number embedded in the row's own label
        # (see segmenter_llm.py's module docstring) -- the input already
        # contains the correct value, the model just doesn't always use
        # it. A deterministic completeness check
        # (segmenter_llm.py:_table_rows_complete) retries just the
        # affected batch with a stronger model, rather than paying for it
        # on every page. Optional: configs that don't define this call
        # site get no escalation.
        escalation_llm_client = None
        if "proposition_segmenter_escalation" in config.llm.call_sites:
            escalation_model = config.llm.call_sites["proposition_segmenter_escalation"].model
            raw_escalation_client = build_llm_client(
                "proposition_segmenter_escalation", config, schema_key="proposition_segmenter"
            )
            escalation_llm_client = _tracked_client(
                raw_escalation_client, escalation_model, usage, usage_lock, spend_state, max_spend_usd, doc_name
            )

        # Per-batch checkpointing guards against losing a whole document's
        # progress to a crash mid-segmentation. Loads any batches a prior,
        # crashed attempt at this document already completed --
        # segment_pages will not re-send those to the LLM.
        already_completed_batches = _load_completed_batches(checkpoint_path, doc_name)
        completed_batches_state: dict[int, list[SegmentedProposition]] = dict(already_completed_batches)
        batches_lock = threading.Lock()

        def _on_batch_start(batch_index: int, total_batches: int, first_page_number: int) -> None:
            _log(f"  {doc_name}: batch {batch_index + 1}/{total_batches} starting (page {first_page_number})")

        def _on_batch_complete(batch_index: int, batch_results: list[SegmentedProposition]) -> None:
            with batches_lock:
                completed_batches_state[batch_index] = batch_results
                _save_completed_batches(checkpoint_path, doc_name, completed_batches_state)

        stop_tpm_logger = threading.Event()
        _start_tpm_logger(llm_client, stop_tpm_logger)
        try:
            segmented = segment_pages(
                kept_pages, document_context, llm_client, batch_size=2, max_workers=max_workers,
                escalation_llm_client=escalation_llm_client,
                on_batch_start=_on_batch_start,
                on_batch_complete=_on_batch_complete,
                completed_batches=already_completed_batches,
                # Raised above the sequential default: under max_workers>1,
                # "consecutive" means consecutive in completion order, not
                # document order (segmenter_llm.py's own docstring flags
                # this as an approximation) -- several unrelated isolated
                # failures can coincidentally land together in completion
                # order under concurrency in a way they never would
                # sequentially, so a threshold tuned for the sequential case
                # can trip too eagerly here without any real systemic cause.
                # A genuine outage still produces far more than 10 failures
                # in a row.
                max_consecutive_batch_failures=10,
            )
        finally:
            stop_tpm_logger.set()

        propositions = _assemble_propositions(segmented, document_context, llm_client)
        elapsed = time.monotonic() - start
        _clear_batch_sidecar(checkpoint_path, doc_name)  # document complete -- no longer needed

        sidecar = _propositions_sidecar_path(checkpoint_path, doc_name)
        sidecar.write_text(
            json.dumps([p.model_dump(mode="json") for p in propositions]), encoding="utf-8"
        )
        state["completed"][doc_name] = {
            "pages_total": len(parsed.pages),
            "pages_skipped": skipped_pages,
            "llm_requests": usage["requests"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "elapsed_seconds": elapsed,
            # Not re-derived from usage["input_tokens"]/["output_tokens"] --
            # when escalation fires (above), those aggregate two
            # differently-priced models' tokens, and a single-price
            # re-derivation would silently misprice whichever model's
            # tokens got summed under the other's rate. usage["cost_usd"]
            # is accumulated per-call, at each call's own model price, by
            # _tracked_client.
            "cost_usd": usage["cost_usd"],
            "proposition_count": len(propositions),
        }
        if "call_site_manifest" not in state:
            # Persisted once, at the checkpoint level (not per-document): a
            # later run that resumes every remaining document from
            # checkpoint never builds a fresh client, so it would otherwise
            # have no live call to read seed_supported/adapter_version
            # from at all.
            state["call_site_manifest"] = asdict(llm_client.to_manifest("proposition_segmenter"))
        _save_checkpoint(checkpoint_path, state)

        all_propositions.extend(propositions)
        total_requests += usage["requests"]
        total_input_tokens += usage["input_tokens"]
        total_output_tokens += usage["output_tokens"]
        total_cost_usd += usage["cost_usd"]
        total_elapsed += elapsed
        if call_site_manifest is None:
            call_site_manifest = llm_client.to_manifest("proposition_segmenter")
        _log(f"finished {doc_name}: {len(propositions)} propositions, {usage['requests']} requests, {elapsed:.1f}s")
        if progress_callback:
            progress_callback(f"{doc_name}: {len(propositions)} propositions in {elapsed:.1f}s")

    gold_overlap_clean: Optional[bool] = None
    if gold_pages_by_doc is not None:
        overlaps: list[GoldPageOverlap] = find_gold_page_overlaps(gold_pages_by_doc, skipped_pages_by_doc)
        gold_overlap_clean = len(overlaps) == 0
        if overlaps:
            _log(f"GOLD OVERLAP DETECTED: {overlaps}")

    skipped_pages_aggregated: dict[str, int] = {}
    for doc_skips in skipped_pages_by_doc.values():
        for reason in doc_skips.values():
            skipped_pages_aggregated[reason] = skipped_pages_aggregated.get(reason, 0) + 1

    build_manifest = build_index(
        all_propositions,
        embedder,
        dense_index_path,
        postgres_dsn,
        indexed_doc_names=doc_names,
        skipped_pages_by_reason=skipped_pages_aggregated,
        gold_overlap_clean=gold_overlap_clean,
    )

    # Not re-derived from total_input_tokens/total_output_tokens with the
    # default single price -- when escalation fires for any document, those
    # totals mix two differently-priced models' tokens (see
    # _tracked_client's docstring above). total_cost_usd is the sum of each
    # document's own correctly-priced cost_usd, resumed or fresh alike.
    total_cost = total_cost_usd
    # Wall-clock speedup implied by this run's own per-document timings
    # against the sequential-baseline rate established by an earlier pilot
    # run (data/pilot_checkpoint.json: 4825.8s / 73 requests).
    _SEQUENTIAL_BASELINE_SECONDS_PER_REQUEST = 4825.836427100003 / 73
    effective_concurrency = (
        (total_requests * _SEQUENTIAL_BASELINE_SECONDS_PER_REQUEST) / total_elapsed
        if total_elapsed > 0 and total_requests > 0
        else None
    )

    return RunManifest(
        config_hash=config_hash,
        index_build=build_manifest,
        llm_call_sites=(call_site_manifest,) if call_site_manifest else (),
        total_requests=total_requests,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cost_usd=total_cost,
        effective_concurrency=effective_concurrency,
    )
