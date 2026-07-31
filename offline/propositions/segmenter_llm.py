"""Proposition segmenter — Path A.

Splits parsed prose into atomic, self-contained propositions using an LLM
call plus a mandatory deterministic validator.

Numeric-verbatim guard (mandatory): every numeric token in a segmented
proposition must appear verbatim in `source_span`, checked via
`contracts.numeric.appears_verbatim` — the same function the FFF rewriter's
guard and CoVe's comparator use downstream, so all the numeric guards in
the system agree by construction rather than by convention. A proposition
failing the guard is dropped, never repaired.

`llm_client` is passed in rather than constructed here (dependency
injection), so this module never imports a provider SDK itself (only
`llm/client.py` is allowed to, per `tests/test_no_direct_sdk_imports.py`)
and the guard logic is testable with a scripted fake client
(`tests/test_proposition_segmenter.py`) without a real provider wired in.

This module returns raw segmented text, not a full `Proposition` — the
`numeric` and `metadata` fields get filled in by the numeric extractor and
metadata tagger downstream, as a separate, later stage.

Page batching (`segment_pages`): two pages per LLM call, to fit a full
indexing run inside provider rate limits and cut repeated system-prompt
overhead. Page attribution is never trusted from the model — each returned
proposition is assigned to whichever page in its batch has the highest
token overlap with its text, deterministically. That's what keeps citation
binding correct across a multi-page batch call.

Structured table rendering: `layout_parser.py`'s `page.tables`
(pdfplumber's ruling-line-based `extract_tables()`) correctly separates a
balance-sheet row's label from its value columns, e.g.
`["Accounts receivable — net of allowances of $174 and $189", "4,532",
"4,660"]`. But feeding the segmenter only the flattened prose text loses
that structure: a row whose label itself contains numbers ("net of
allowances of $174 and $189") sits directly beside the row's real value
("4,532") with no structural cue telling them apart, and the model has to
guess which number is the fact. `_render_page_batch` renders each page's
already-extracted `page.tables` (row | column, preserving position) into
`source_span` alongside the prose, with an explicit system-prompt rule to
prefer a table's own value column over any number embedded in a row's
label. It's appended into `source_span` itself, not a separate payload
field, so the numeric-verbatim guard still checks a proposition's digits
against the same string a table-sourced fact was read from.
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
    """Calls the LLM, then applies the deterministic numeric-verbatim guard:
    any proposition containing a numeric token absent from `source_span` is
    dropped, not repaired. Retries up to `max_attempts` times if the model
    returns zero propositions for non-empty input; if it still returns
    none, the span is skipped — never invented.
    """
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
            # A call that hit a length limit, a refusal, or a malformed
            # response is treated exactly like "returned nothing usable" —
            # retry, then drop this span, never let one bad response crash
            # the document or the run (llm/adapters/base.py:LLMCallFailed).
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
    row/column structure `page.text`'s flattened prose loses, so it never
    has to guess which number in a row is the value versus a number merely
    embedded in the row's own label.

    Blank cells are dropped deliberately: `pdfplumber` splits some
    ruling-line layouts into more cells than others for the identical
    logical shape — one balance sheet's AR row extracts as
    `[label, "", "", "5,020", "", "", "4,911", ""]` (value at position 3),
    another's as `[label, "", "4,532", None, "", "4,660", None]` (value at
    position 2). Rendering every cell position-for-position, including the
    blanks, would preserve that inconsistency into the prompt and give the
    model no reliable positional cue. Dropping empty cells before joining
    collapses both shapes to the same `label | 5,020 | 4,911` form,
    removing the positional ambiguity instead of encoding it."""
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
    """Word-overlap tokens, common short words excluded — two unrelated
    labels sharing only "of"/"net"/"and" must not read as the same row
    (e.g. "net of allowances" and "total revenue of $X" would otherwise
    tie on "of" alone)."""
    return set(_WORD_RE.findall(text.lower())) - _STOPWORDS


def _rows_with_label_embedded_numbers(tables: Sequence[Table]) -> list[tuple[str, list[str]]]:
    """Every table row whose label cell itself contains a numeric token
    *and* has its own separate, non-empty value cell(s) beyond the label —
    the specific shape where a value-column number risks being confused
    with, or overshadowed by, a number embedded in the row's own label
    (e.g. "Accounts receivable — net of allowances of $174 and $189" next
    to its real value, "4,532"). Not filing-specific: fires on this row
    *shape*, wherever it occurs."""
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
    """Deterministic completeness check. True iff, for every table row whose
    label contains a number and which has its own value column(s), at
    least one proposition about that row also states one of the row's real
    values. Catches a segmentation that restated only the label's embedded
    decoy number and silently dropped the row's actual value.

    A row never segmented into any proposition at all is not this guard's
    concern (that's an ordinary drop, already handled elsewhere) — only a
    row that *was* written about, incompletely, trips this."""
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
    """Every page in the batch tied for the highest token overlap with
    `proposition_text` — usually one page, but a line restated verbatim on
    two pages (e.g. a 10-K's "Net income including noncontrolling interest"
    closing the Income Statement and, word-for-word, opening the
    Comprehensive Income statement on the next page) ties on every token,
    since the text is byte-identical on both pages. Order matches `pages`
    (ascending page number, by construction of the batch)."""
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
    """Deterministic page attribution for a whole batch at once — never
    trusted from the model. Unambiguous propositions (token overlap highest
    on exactly one page) are assigned directly.

    Tie-break: a tie across pages happens almost exclusively when identical
    text is genuinely repeated across pages, and the segmenter emits one
    proposition per source occurrence, not one per distinct fact — so a
    restated line produces two identical-text propositions, one per
    occurrence. Always picking "first page in batch order" for a tied
    proposition would collapse both occurrences onto the same, earlier
    page, silently fabricating the later one's citation.

    Batch ordering must never determine provenance, so instead of a static
    tie-break rule, duplicate emissions are paired with their duplicate
    pages. Group propositions by identical text; within a group
    that ties across the same N pages and itself contains N (or more)
    occurrences, assign the k-th occurrence (in the order the model emitted
    them) to the k-th tied page in ascending page-number order — the
    originating page for that specific occurrence, not a batch-wide
    default. A tie with no matching duplicate sibling (a fact that happens
    to tie across pages but was only emitted once) falls back to the
    original first-tied-page default, since there is no signal in the
    output that disambiguates it further.
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
    """Raised by `segment_pages` when too many page-batches in a row come
    back with zero propositions from non-blank input. An isolated per-batch
    failure (one huge table, one content-filter false positive) is expected
    and handled by dropping just that batch; a long run of consecutive
    empty batches is a different signal — a systemic problem (bad
    credentials, persistent rate limiting, a network outage) that would
    otherwise silently produce a document with few or no propositions
    instead of failing loudly."""


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
    """Batched version of segmentation. Reuses `segment_propositions` (and
    therefore its numeric-verbatim guard, unchanged) over each batch's
    combined text; page attribution is computed afterward, deterministically,
    never read from the model's output.

    `max_workers`: `max_workers=1` (default) runs batches sequentially, one
    LLM call at a time. `max_workers>1` dispatches batches to a
    `ThreadPoolExecutor`, submitted `max_workers` at a time; the OpenAI
    SDK's client is documented thread-safe for concurrent requests, and
    `RoutedLLMClient`'s `RateLimiter` is itself thread-safe (a shared
    sliding window), so concurrent callers correctly share one RPM/TPM
    budget rather than each assuming the full limit. Results are re-sorted
    into original batch order before being returned, so output is
    identical to the sequential path regardless of which batch's call
    happens to finish first.

    The circuit breaker still trips on `max_consecutive_batch_failures`,
    but concurrently "consecutive" means consecutive in completion order,
    not original batch order — batches finish out of order under
    concurrency, and completion order is the only order in which failures
    are actually observed as they happen. This is an approximation of the
    sequential semantics, not identical to it, but still trips promptly on
    a systemic failure (bad credentials, persistent rate limiting) rather
    than only after every batch has been dispatched.

    `escalation_llm_client`: optional. When a batch's result fails
    `_table_rows_complete` (a table row's real value was dropped), that one
    batch is retried with this client instead of `llm_client`, rather than
    escalating the whole call site to a pricier model for every page.
    `None` (the default) disables escalation entirely.

    Per-batch checkpointing and diagnostics, since document-level
    checkpointing alone loses a whole document's progress on retry and
    gives no visibility into which page-batch was in flight when a call
    failed:
    - `on_batch_start(batch_index, total_batches, first_page_number)`,
      called immediately before each batch's LLM call — the caller (e.g.
      `full_build.py`'s own logger) can log this so the *last* logged batch
      is known to be the one in flight if a call raises.
    - `on_batch_complete(batch_index, batch_results)`, called immediately
      after each batch succeeds — the caller can persist per-batch progress
      incrementally (e.g. a sidecar file updated after every batch) rather
      than only at document end.
    - `completed_batches`, a `{batch_index: [SegmentedProposition, ...]}`
      map of batches already completed in a *prior* attempt at this same
      document — those batch indices are never re-sent to the LLM; a resume
      after a crash mid-document only re-processes the batch that was
      actually in flight (and anything after it), not the whole document.
    All three are optional and default to no-ops/empty — existing callers
    are unaffected.
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
        # The primary model dropped a table row's real value -- retry this
        # one batch with a stronger escalation model rather than the whole
        # call site. The escalation result is used unconditionally once
        # triggered, whether or not it also passes the completeness check
        # -- the budget for this batch is spent either way, and the
        # escalation model is presumed the better of the two attempts.
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
        # on_batch_start fires here, inside the worker thread, right before
        # the LLM call actually begins -- not at ThreadPoolExecutor.submit()
        # time in the main thread. Firing it at submission would log every
        # pending batch as "started" immediately regardless of max_workers,
        # which defeats the point of the diagnostic.
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
