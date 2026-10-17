"""Proposition segmenter — Path A. Splits parsed prose into atomic,
self-contained propositions via an LLM call plus a mandatory deterministic
validator.

Numeric-verbatim guard: every numeric token must appear verbatim in
source_span (contracts.numeric.appears_verbatim — same function the FFF
guard and CoVe comparator use, so all guards agree by construction). A
proposition failing the guard is dropped, never repaired.

llm_client is injected, not constructed here, so this module never imports
a provider SDK and the guard is testable with a scripted fake client.

Returns raw segmented text, not a full Proposition — numeric/metadata
fields get filled in downstream by later stages.

Page batching: two pages per call, to fit rate limits and cut prompt
overhead. Page attribution is never trusted from the model — each
proposition is assigned deterministically to whichever page in its batch
has the highest token overlap with its text.

Table rendering: pdfplumber's page.tables correctly separates a row's
label from its value columns, but flattened prose text loses that
structure and the model has to guess which number is the real value.
_render_page_batch renders tables (row | column) into source_span itself,
with a prompt rule to prefer the value column over a label-embedded
number — appended into source_span, not a separate field, so the
numeric-verbatim guard still checks against the same string.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from contracts.numeric import appears_verbatim, normalize_value, numeric_tokens
from llm.adapters.base import LLMCallFailed
from llm.client import LLMClient
from offline.parsers.layout_parser import ParsedPage, Table

SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """\
You split financial document text into atomic propositions.

An atomic proposition is a single self-contained factual statement that concerns
exactly one entity, one metric, and one period where those apply. It must be
understandable without surrounding context: restate the subject explicitly rather
than using pronouns or references like "the company" or "that year".

RULES
- Copy every number, date, and identifier EXACTLY as it appears in the source.
  Never round, reformat, recompute, or infer a numeric value.
- Do not add facts. If the source does not state something, it does not appear.
- Do not merge two facts into one proposition.
- Narrative text with no financial figures still yields propositions; segment it
  the same way.
- If a sentence contains multiple line items or multiple periods, emit one
  proposition per item/period combination.
- When a page includes a TABLE section, use the table's own row/column structure
  to find each line item's value. A row's label may itself contain a number that
  is NOT the value (e.g. "Accounts receivable — net of allowances of $174 and
  $189" states an allowance amount inside the label; the row's actual value is
  in its own column, not that embedded number). Never take a number from inside
  a label as the fact's value when the table gives that row its own value column.

  Worked example — a table row rendered as:
    Accounts receivable - net of allowances of $174 and $189 | 4,532 | 4,660
  is a SINGLE line item ("Accounts receivable - net of allowances") with TWO
  period columns. $174 and $189 are allowance amounts embedded in the label —
  never emit these as the accounts receivable value. The two real values are
  4,532 and 4,660, one per column. Correct propositions:
    "3M Company had accounts receivable of $4,532 million at December 31, 2022."
    "3M Company had accounts receivable of $4,660 million at December 31, 2021."
  A proposition stating only the allowance ("...net of allowances of $174
  million...") without ever giving the row's own value is WRONG and incomplete
  — the allowance detail may be included, but the row's real value must always
  be stated too.
"""


@dataclass(frozen=True)
class SegmentedProposition:
    text: str
    source_offset_hint: Optional[str]
    page_number: Optional[int] = None  # set by segment_pages; None for the single-span path


def segment_propositions(
    source_span: str,
    document_context: dict[str, Any],
    llm_client: LLMClient,
    max_attempts: int = 2,
) -> list[SegmentedProposition]:
    """Calls the LLM, then applies the numeric-verbatim guard: any
    proposition with a token absent from source_span is dropped. Retries
    up to max_attempts on empty results; still-empty spans are skipped."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "source_span": source_span,
        "document_context": document_context,
    }

    raw_propositions: list[dict[str, Any]] = []
    for _ in range(max_attempts):
        try:
            response = llm_client.complete(payload)
            raw_propositions = response.get("propositions", [])
        except LLMCallFailed:
            # Length limit, refusal, or malformed response all treated as
            # "nothing usable" -- retry, then drop, never crash the run.
            raw_propositions = []
        if raw_propositions or not source_span.strip():
            break

    accepted: list[SegmentedProposition] = []
    for raw in raw_propositions:
        text = raw.get("text", "")
        if not appears_verbatim(text, source_span):
            continue  # numeric-verbatim guard: drop, never repair
        accepted.append(
            SegmentedProposition(text=text, source_offset_hint=raw.get("source_offset_hint"))
        )
    return accepted


_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _render_table(table: Table) -> str:
    """Row | column values, blank cells dropped. Gives the model the
    row/column structure flattened prose loses, so it never has to guess
    which number is the value vs. one embedded in the label.

    Blanks dropped because pdfplumber splits identical logical rows into
    different cell counts across tables (value at position 2 vs 3) --
    dropping empties collapses both shapes to the same form instead of
    baking the inconsistency into the prompt."""
    lines = []
    for row in table:
        cells = [cell for cell in row if cell]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _numeric_values(text: str) -> set:
    return {v for v in (normalize_value(tok) for tok in numeric_tokens(text)) if v is not None}


_STOPWORDS = {
    "a", "an", "and", "at", "for", "in", "is", "net", "of", "on", "or", "the", "to", "was", "were",
}


def _significant_words(text: str) -> set:
    """Word-overlap tokens, stopwords excluded — two unrelated labels
    sharing only "of"/"net"/"and" must not read as the same row."""
    return set(_WORD_RE.findall(text.lower())) - _STOPWORDS


def _rows_with_label_embedded_numbers(tables: Sequence[Table]) -> list[tuple[str, list[str]]]:
    """Every row whose label contains a number *and* has its own separate
    value cell(s) — the shape where a value risks being confused with a
    number embedded in the label. Fires on the row shape, not any filing specifically."""
    rows: list[tuple[str, list[str]]] = []
    for table in tables:
        for row in table:
            if not row:
                continue
            label = row[0]
            value_cells = [c for c in row[1:] if c]
            if label and numeric_tokens(label) and value_cells:
                rows.append((label, value_cells))
    return rows


def _table_rows_complete(pages: Sequence[ParsedPage], propositions: Sequence[SegmentedProposition]) -> bool:
    """True iff every table row with a label-embedded number and its own
    value column has at least one proposition stating a real value —
    catches a segmentation that restated only the label's decoy number.

    A row never segmented at all is not this guard's concern; only a row
    written about incompletely trips it."""
    rows = _rows_with_label_embedded_numbers([t for page in pages for t in page.tables])
    if not rows:
        return True

    for label, value_cells in rows:
        label_words = _significant_words(label)
        related = [p for p in propositions if label_words & _significant_words(p.text)]
        if not related:
            continue
        related_values = _numeric_values(" ".join(p.text for p in related))
        row_values = _numeric_values(" ".join(value_cells))
        if not (row_values & related_values):
            return False
    return True


def _render_page_batch(pages: Sequence[ParsedPage]) -> str:
    sections = []
    for page in pages:
        section = f"--- PAGE {page.page_number} ---\n{page.text}"
        if page.tables:
            rendered_tables = "\n\n".join(_render_table(t) for t in page.tables)
            section += (
                f"\n\n--- PAGE {page.page_number} TABLES (row | column values — "
                f"use these for exact figures, not numbers embedded in a row's "
                f"own label) ---\n{rendered_tables}"
            )
        sections.append(section)
    return "\n\n".join(sections)


def _candidate_pages(proposition_text: str, pages: Sequence[ParsedPage]) -> list[int]:
    """Pages tied for highest token overlap with proposition_text — usually
    one, but a line restated verbatim across two pages ties on every token.
    Order matches pages (ascending page number)."""
    prop_tokens = set(_WORD_RE.findall(proposition_text.lower()))
    best_overlap = -1
    best_pages: list[int] = []
    for page in pages:
        page_tokens = set(_WORD_RE.findall(page.text.lower()))
        overlap = len(prop_tokens & page_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_pages = [page.page_number]
        elif overlap == best_overlap:
            best_pages.append(page.page_number)
    return best_pages


def _attribute_pages(propositions: Sequence["SegmentedProposition"], pages: Sequence[ParsedPage]) -> list[int]:
    """Deterministic page attribution for a whole batch — never trusted
    from the model. Unambiguous props (overlap highest on one page) assign
    directly.

    Tied props usually mean genuinely repeated text, with one proposition
    emitted per occurrence — always picking the first tied page would
    fabricate the later occurrence's citation. Instead, duplicate emissions
    within a tie group are paired to their duplicate pages in emission
    order; a tie with no duplicate sibling falls back to the first tied
    page, since there's no signal to disambiguate further.
    """
    candidates = [_candidate_pages(prop.text, pages) for prop in propositions]

    tie_groups: dict[tuple[str, tuple[int, ...]], list[int]] = {}
    for i, prop in enumerate(propositions):
        if len(candidates[i]) > 1:
            key = (prop.text, tuple(candidates[i]))
            tie_groups.setdefault(key, []).append(i)

    result: list[Optional[int]] = [None] * len(propositions)
    for (_, tied_pages), indices in tie_groups.items():
        if len(indices) > 1:
            for rank, index in enumerate(indices):  # `indices` is already in emission order
                result[index] = tied_pages[min(rank, len(tied_pages) - 1)]

    for i in range(len(propositions)):
        if result[i] is None:
            result[i] = candidates[i][0]  # unambiguous, or an unresolved lone tie (see docstring)

    return result  # type: ignore[return-value]


class TooManyConsecutiveBatchFailures(RuntimeError):
    """Too many consecutive batches came back empty from non-blank input.
    An isolated failure is expected and just dropped; a long run signals a
    systemic problem (bad creds, rate limiting, outage) worth failing loudly."""


def _make_batches(pages: Sequence[ParsedPage], batch_size: int) -> list[list[ParsedPage]]:
    return [list(pages[i : i + batch_size]) for i in range(0, len(pages), batch_size)]


def segment_pages(
    pages: Sequence[ParsedPage],
    document_context: dict[str, Any],
    llm_client: LLMClient,
    batch_size: int = 2,
    max_attempts: int = 2,
    max_consecutive_batch_failures: int = 5,
    max_workers: int = 1,
    escalation_llm_client: Optional[LLMClient] = None,
    on_batch_start: Optional[Callable[[int, int, int], None]] = None,
    on_batch_complete: Optional[Callable[[int, list[SegmentedProposition]], None]] = None,
    completed_batches: Optional[dict[int, list[SegmentedProposition]]] = None,
) -> list[SegmentedProposition]:
    """Batched segmentation. Reuses segment_propositions (and its guard)
    per batch; page attribution is computed after, deterministically,
    never read from the model.

    max_workers=1 (default) runs sequentially. >1 dispatches to a
    ThreadPoolExecutor sharing one RPM/TPM budget via RateLimiter; results
    are re-sorted to original batch order regardless of completion order.

    The failure circuit breaker trips on completion order under
    concurrency (batches finish out of order), an approximation of
    sequential semantics that still trips promptly on a real outage.

    escalation_llm_client: optional retry client for a batch that fails
    _table_rows_complete, instead of escalating every page. None disables it.

    on_batch_start/on_batch_complete/completed_batches give per-batch
    checkpointing and diagnostics beyond document-level checkpointing, so
    a resume after a crash only reprocesses the batch that was actually
    in flight. All optional, default to no-ops.
    """
    batches = _make_batches(pages, batch_size)
    completed_batches = completed_batches or {}
    if max_workers <= 1:
        return _segment_batches_sequential(
            batches, document_context, llm_client, max_attempts, max_consecutive_batch_failures,
            escalation_llm_client, on_batch_start, on_batch_complete, completed_batches,
        )
    return _segment_batches_concurrent(
        batches, document_context, llm_client, max_attempts, max_consecutive_batch_failures, max_workers,
        escalation_llm_client, on_batch_start, on_batch_complete, completed_batches,
    )


def _segment_one_batch(
    batch: list[ParsedPage],
    document_context: dict[str, Any],
    llm_client: LLMClient,
    max_attempts: int,
    escalation_llm_client: Optional[LLMClient] = None,
) -> list[SegmentedProposition]:
    combined_span = _render_page_batch(batch)
    segmented = segment_propositions(combined_span, document_context, llm_client, max_attempts=max_attempts)

    if escalation_llm_client is not None and not _table_rows_complete(batch, segmented):
        # Primary model dropped a table row's real value -- retry just this
        # batch on a stronger model. Result used unconditionally once triggered,
        # even if it also fails the completeness check.
        segmented = segment_propositions(
            combined_span, document_context, escalation_llm_client, max_attempts=max_attempts
        )

    page_numbers = _attribute_pages(segmented, batch)
    return [
        SegmentedProposition(text=prop.text, source_offset_hint=prop.source_offset_hint, page_number=page_number)
        for prop, page_number in zip(segmented, page_numbers)
    ]


def _segment_batches_sequential(
    batches: list[list[ParsedPage]],
    document_context: dict[str, Any],
    llm_client: LLMClient,
    max_attempts: int,
    max_consecutive_batch_failures: int,
    escalation_llm_client: Optional[LLMClient] = None,
    on_batch_start: Optional[Callable[[int, int, int], None]] = None,
    on_batch_complete: Optional[Callable[[int, list[SegmentedProposition]], None]] = None,
    completed_batches: Optional[dict[int, list[SegmentedProposition]]] = None,
) -> list[SegmentedProposition]:
    completed_batches = completed_batches or {}
    results: list[SegmentedProposition] = []
    consecutive_failures = 0
    for index, batch in enumerate(batches):
        if index in completed_batches:
            results.extend(completed_batches[index])
            consecutive_failures = 0
            continue

        if on_batch_start is not None:
            on_batch_start(index, len(batches), batch[0].page_number)

        combined_span = _render_page_batch(batch)
        batch_results = _segment_one_batch(batch, document_context, llm_client, max_attempts, escalation_llm_client)

        if not batch_results and combined_span.strip():
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_batch_failures:
                raise TooManyConsecutiveBatchFailures(
                    f"segment_pages: {consecutive_failures} consecutive page-batches produced "
                    "zero propositions from non-blank input — this looks like a systemic issue, "
                    "not isolated per-batch content problems. Stopping rather than silently "
                    f"producing a near-empty document (batch starting at page "
                    f"{batch[0].page_number})."
                )
        else:
            consecutive_failures = 0

        if on_batch_complete is not None:
            on_batch_complete(index, batch_results)

        results.extend(batch_results)
    return results


def _segment_batches_concurrent(
    batches: list[list[ParsedPage]],
    document_context: dict[str, Any],
    llm_client: LLMClient,
    max_attempts: int,
    max_consecutive_batch_failures: int,
    max_workers: int,
    escalation_llm_client: Optional[LLMClient] = None,
    on_batch_start: Optional[Callable[[int, int, int], None]] = None,
    on_batch_complete: Optional[Callable[[int, list[SegmentedProposition]], None]] = None,
    completed_batches: Optional[dict[int, list[SegmentedProposition]]] = None,
) -> list[SegmentedProposition]:
    completed_batches = completed_batches or {}
    results_by_batch_index: dict[int, list[SegmentedProposition]] = dict(completed_batches)
    consecutive_failures = 0

    def _run_batch_logged(i: int) -> list[SegmentedProposition]:
        # Fires inside the worker thread right before the call, not at submit()
        # time -- otherwise every pending batch logs "started" at once.
        if on_batch_start is not None:
            on_batch_start(i, len(batches), batches[i][0].page_number)
        return _segment_one_batch(batches[i], document_context, llm_client, max_attempts, escalation_llm_client)

    pending_indices = [i for i in range(len(batches)) if i not in completed_batches]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {executor.submit(_run_batch_logged, i): i for i in pending_indices}
        try:
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                batch = batches[index]
                batch_results = future.result()
                results_by_batch_index[index] = batch_results

                if not batch_results and _render_page_batch(batch).strip():
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_batch_failures:
                        raise TooManyConsecutiveBatchFailures(
                            f"segment_pages: {consecutive_failures} consecutive page-batches "
                            "(in completion order) produced zero propositions from non-blank "
                            "input — this looks like a systemic issue, not isolated per-batch "
                            "content problems. Stopping rather than silently producing a "
                            f"near-empty document (batch starting at page {batch[0].page_number})."
                        )
                else:
                    consecutive_failures = 0

                if on_batch_complete is not None:
                    on_batch_complete(index, batch_results)
        finally:
            for future in future_to_index:
                future.cancel()

    results: list[SegmentedProposition] = []
    for index in sorted(results_by_batch_index):
        results.extend(results_by_batch_index[index])
    return results
