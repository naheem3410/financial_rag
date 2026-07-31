"""Proposition segmenter tests. A scripted fake client exercises the real
guard logic (contracts.numeric.appears_verbatim) without a real provider
wired in.
"""
from __future__ import annotations

import threading
import time

import pytest

from llm.adapters.base import LLMCallFailed, LLMInfrastructureError
from offline.parsers.layout_parser import ParsedPage
from offline.propositions.segmenter_llm import (
    SegmentedProposition,
    TooManyConsecutiveBatchFailures,
    segment_pages,
    segment_propositions,
)


class _FakeLLMClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


class _FailThenSucceedLLMClient:
    """Raises LLMCallFailed on its first N calls, then returns real
    responses — simulates a length-limit failure or refusal that resolves
    on retry."""

    def __init__(self, fail_count, responses):
        self._fail_count = fail_count
        self._responses = list(responses)
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        if self.calls <= self._fail_count:
            raise LLMCallFailed("simulated provider failure (e.g. length limit)")
        return self._responses.pop(0)


class _AlwaysFailsLLMClient:
    def __init__(self):
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        raise LLMCallFailed("simulated provider failure (e.g. length limit)")


def test_guard_drops_proposition_with_fabricated_digit():
    source_span = "3M Company reported net sales of $32.2 billion for fiscal year 2018."
    client = _FakeLLMClient(
        [
            {
                "reasoning": "...",
                "propositions": [
                    {
                        "text": "3M Company net sales for fiscal year 2018 were $32.2 billion.",
                        "source_offset_hint": None,
                    },
                    {
                        "text": "3M Company net sales for fiscal year 2018 were $99.9 billion.",
                        "source_offset_hint": None,
                    },
                ],
            }
        ]
    )

    result = segment_propositions(source_span, {"company": "3M"}, llm_client=client)

    texts = [p.text for p in result]
    assert "3M Company net sales for fiscal year 2018 were $32.2 billion." in texts
    assert "3M Company net sales for fiscal year 2018 were $99.9 billion." not in texts


def test_guard_keeps_proposition_with_reformatted_but_genuine_value():
    """18.20 in the source and 18.2 in the candidate are the same value —
    exactly the case contracts.numeric.normalize_value exists to handle."""
    source_span = "Operating margin was 18.20 percent in Q3."
    client = _FakeLLMClient(
        [
            {
                "reasoning": "...",
                "propositions": [
                    {"text": "Operating margin was 18.2 percent in Q3.", "source_offset_hint": None}
                ],
            }
        ]
    )

    result = segment_propositions(source_span, {}, llm_client=client)

    assert len(result) == 1


def test_retries_once_on_empty_propositions_then_succeeds():
    source_span = "3M Company reported net sales of $32.2 billion."
    client = _FakeLLMClient(
        [
            {"reasoning": "...", "propositions": []},
            {
                "reasoning": "...",
                "propositions": [
                    {"text": "3M Company net sales were $32.2 billion.", "source_offset_hint": None}
                ],
            },
        ]
    )

    result = segment_propositions(source_span, {}, llm_client=client)

    assert len(client.calls) == 2
    assert len(result) == 1


def test_gives_up_after_max_attempts_and_returns_empty():
    source_span = "3M Company reported net sales of $32.2 billion."
    client = _FakeLLMClient(
        [
            {"reasoning": "...", "propositions": []},
            {"reasoning": "...", "propositions": []},
        ]
    )

    result = segment_propositions(source_span, {}, llm_client=client, max_attempts=2)

    assert result == []
    assert len(client.calls) == 2


def test_empty_source_span_does_not_retry():
    client = _FakeLLMClient([{"reasoning": "...", "propositions": []}])

    result = segment_propositions("   ", {}, llm_client=client)

    assert result == []
    assert len(client.calls) == 1


class TestSegmentPagesBatching:
    def test_two_pages_produce_one_llm_call_not_two(self):
        pages = [
            ParsedPage(page_number=0, text="Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", tables=[]),
            ParsedPage(page_number=1, text="Acme Corp net sales for Q3 FY2024 were $8.5 billion.", tables=[]),
        ]
        client = _FakeLLMClient(
            [
                {
                    "reasoning": "...",
                    "propositions": [
                        {"text": "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", "source_offset_hint": None},
                        {"text": "Acme Corp net sales for Q3 FY2024 were $8.5 billion.", "source_offset_hint": None},
                    ],
                }
            ]
        )

        result = segment_pages(pages, {}, llm_client=client, batch_size=2)

        assert len(client.calls) == 1  # one call for the whole 2-page batch
        assert len(result) == 2

    def test_page_attribution_is_computed_not_trusted_from_the_model(self):
        """The model output has no page field at all — attribution is
        purely a function of token overlap against each page's own text."""
        pages = [
            ParsedPage(page_number=10, text="Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", tables=[]),
            ParsedPage(page_number=11, text="Acme Corp net sales for Q3 FY2024 were $8.5 billion.", tables=[]),
        ]
        client = _FakeLLMClient(
            [
                {
                    "reasoning": "...",
                    "propositions": [
                        {"text": "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", "source_offset_hint": None},
                        {"text": "Acme Corp net sales for Q3 FY2024 were $8.5 billion.", "source_offset_hint": None},
                    ],
                }
            ]
        )

        result = segment_pages(pages, {}, llm_client=client, batch_size=2)

        by_text = {p.text: p.page_number for p in result}
        assert by_text["Acme Corp operating margin for Q3 FY2024 was 18.2 percent."] == 10
        assert by_text["Acme Corp net sales for Q3 FY2024 were $8.5 billion."] == 11

    def test_three_pages_with_batch_size_two_makes_two_calls(self):
        # max_attempts=1: segment_propositions retries on an empty result
        # for a non-blank span, which would otherwise consume more than one
        # fake response per batch and obscure the thing under test — the
        # number of *batches*, not the retry behavior already covered by
        # test_retries_once_on_empty_propositions_then_succeeds above.
        pages = [
            ParsedPage(page_number=0, text="Page zero text about Acme Corp.", tables=[]),
            ParsedPage(page_number=1, text="Page one text about Acme Corp.", tables=[]),
            ParsedPage(page_number=2, text="Page two text about Acme Corp.", tables=[]),
        ]
        client = _FakeLLMClient(
            [
                {"reasoning": "...", "propositions": []},
                {"reasoning": "...", "propositions": []},
            ]
        )

        segment_pages(pages, {}, llm_client=client, batch_size=2, max_attempts=1)

        assert len(client.calls) == 2  # [page0, page1], [page2]

    def test_numeric_verbatim_guard_still_applies_across_a_batch(self):
        """A fabricated digit not present on either page in the batch must
        still be dropped — batching must not weaken the guard."""
        pages = [
            ParsedPage(page_number=0, text="Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", tables=[]),
            ParsedPage(page_number=1, text="Acme Corp net sales for Q3 FY2024 were $8.5 billion.", tables=[]),
        ]
        client = _FakeLLMClient(
            [
                {
                    "reasoning": "...",
                    "propositions": [
                        {"text": "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", "source_offset_hint": None},
                        {"text": "Acme Corp operating margin for Q3 FY2024 was 99.9 percent.", "source_offset_hint": None},
                    ],
                }
            ]
        )

        result = segment_pages(pages, {}, llm_client=client, batch_size=2)

        texts = [p.text for p in result]
        assert "Acme Corp operating margin for Q3 FY2024 was 18.2 percent." in texts
        assert "Acme Corp operating margin for Q3 FY2024 was 99.9 percent." not in texts


class TestCrossPageDuplicateAttribution:
    """Regression, 2026-07-25: reviewing a real 3M_2018_10K sample found 3
    of 75 propositions carrying a wrong citation page. "Net income
    including noncontrolling interest" closes the Income Statement (page
    55) and, word-for-word, opens the Comprehensive Income statement (page
    56) — standard 10-K structure. Both occurrences were correctly emitted
    as separate propositions, but the old tie-break ("first page in batch
    order") attributed both to page 55, fabricating page 56's citation.
    Citation correctness is an invariant; batch ordering must never
    determine provenance."""

    def test_a_sentence_appearing_verbatim_on_two_pages_attributes_each_occurrence_to_its_own_page(self):
        pages = [
            ParsedPage(
                page_number=55,
                text="Consolidated Statement of Income. Net income including noncontrolling interest $ 5,363 million.",
                tables=[],
            ),
            ParsedPage(
                page_number=56,
                text="Consolidated Statement of Comprehensive Income. Net income including noncontrolling interest $ 5,363 million.",
                tables=[],
            ),
        ]
        # Both pages contain the identical restated sentence -- the model
        # emits it twice, once per real source occurrence, in reading order
        # (page 55's Income Statement first, page 56's Comprehensive Income
        # statement second), exactly like the real 3M sample.
        client = _FakeLLMClient(
            [
                {
                    "reasoning": "...",
                    "propositions": [
                        {"text": "Acme Corp had net income including noncontrolling interest of $5,363 million.", "source_offset_hint": None},
                        {"text": "Acme Corp had net income including noncontrolling interest of $5,363 million.", "source_offset_hint": None},
                    ],
                }
            ]
        )

        result = segment_pages(pages, {}, llm_client=client, batch_size=2)

        assert len(result) == 2
        assert [p.page_number for p in result] == [55, 56]

    def test_three_repeated_occurrences_pair_with_three_tied_pages_in_order(self):
        """Generalizes beyond exactly-2: N identical emissions tied across
        N pages pair up one-to-one, in order, not just the 2-page case."""
        shared_text = "Segment revenue was $100 million."
        pages = [
            ParsedPage(page_number=10, text=shared_text, tables=[]),
            ParsedPage(page_number=11, text=shared_text, tables=[]),
            ParsedPage(page_number=12, text=shared_text, tables=[]),
        ]
        client = _FakeLLMClient(
            [{"reasoning": "...", "propositions": [{"text": shared_text, "source_offset_hint": None}] * 3}]
        )

        result = segment_pages(pages, {}, llm_client=client, batch_size=3)

        assert [p.page_number for p in result] == [10, 11, 12]

    def test_a_genuinely_unambiguous_proposition_is_unaffected(self):
        """The fix must not disturb the common case: a proposition whose
        text appears on exactly one page in the batch."""
        pages = [
            ParsedPage(page_number=0, text="Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", tables=[]),
            ParsedPage(page_number=1, text="Acme Corp net sales for Q3 FY2024 were $8.5 billion.", tables=[]),
        ]
        client = _FakeLLMClient(
            [
                {
                    "reasoning": "...",
                    "propositions": [
                        {"text": "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", "source_offset_hint": None},
                        {"text": "Acme Corp net sales for Q3 FY2024 were $8.5 billion.", "source_offset_hint": None},
                    ],
                }
            ]
        )

        result = segment_pages(pages, {}, llm_client=client, batch_size=2)

        by_text = {p.text: p.page_number for p in result}
        assert by_text["Acme Corp operating margin for Q3 FY2024 was 18.2 percent."] == 0
        assert by_text["Acme Corp net sales for Q3 FY2024 were $8.5 billion."] == 1

    def test_a_lone_tie_with_no_duplicate_sibling_falls_back_to_the_first_tied_page(self):
        """A fact that happens to tie across pages but was emitted only
        once (no duplicate sibling to pair against) -- there is no signal
        in the output to disambiguate further, so it falls back to the
        original default rather than raising or guessing."""
        shared_text = "Effective tax rate was 21 percent."
        pages = [
            ParsedPage(page_number=5, text=shared_text, tables=[]),
            ParsedPage(page_number=6, text=shared_text, tables=[]),
        ]
        client = _FakeLLMClient(
            [{"reasoning": "...", "propositions": [{"text": shared_text, "source_offset_hint": None}]}]
        )

        result = segment_pages(pages, {}, llm_client=client, batch_size=2)

        assert len(result) == 1
        assert result[0].page_number == 5  # first tied page, unchanged fallback behavior


class TestLLMCallFailedResilience:
    """Regression tests for the 2026-07-25 pilot crash: a provider call that
    raises LLMCallFailed (length limit, refusal, malformed response) must be
    treated exactly like an empty response — retried, then dropped — and
    must never propagate up and crash the document or the run.
    """

    def test_a_failed_call_that_succeeds_on_retry_is_not_lost(self):
        client = _FailThenSucceedLLMClient(
            fail_count=1,
            responses=[
                {
                    "reasoning": "...",
                    "propositions": [
                        {"text": "Acme Corp net sales were $32.2 billion.", "source_offset_hint": None}
                    ],
                }
            ],
        )

        result = segment_propositions(
            "Acme Corp net sales were $32.2 billion.", {}, llm_client=client, max_attempts=2
        )

        assert client.calls == 2
        assert len(result) == 1

    def test_a_call_that_always_fails_is_dropped_not_raised(self):
        client = _AlwaysFailsLLMClient()

        result = segment_propositions(
            "Acme Corp net sales were $32.2 billion.", {}, llm_client=client, max_attempts=2
        )

        assert client.calls == 2  # respected max_attempts, did not crash
        assert result == []

    def test_one_failed_batch_does_not_abort_the_rest_of_the_document(self):
        """The exact shape of the pilot bug: a huge table on one page-batch
        blows the length limit, but the document has other pages that must
        still get segmented."""
        pages = [
            ParsedPage(page_number=0, text="Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", tables=[]),
            ParsedPage(page_number=1, text="Acme Corp net sales for Q3 FY2024 were $8.5 billion.", tables=[]),
        ]
        client = _FailThenSucceedLLMClient(
            fail_count=2,  # both attempts for batch 1 fail
            responses=[
                {
                    "reasoning": "...",
                    "propositions": [
                        {"text": "Acme Corp net sales for Q3 FY2024 were $8.5 billion.", "source_offset_hint": None}
                    ],
                }
            ],
        )

        result = segment_pages(pages, {}, llm_client=client, batch_size=1, max_attempts=2)

        assert len(result) == 1
        assert result[0].text == "Acme Corp net sales for Q3 FY2024 were $8.5 billion."


class TestConsecutiveFailureCircuitBreaker:
    def test_isolated_failures_never_trip_the_breaker(self):
        """One failed batch among many healthy ones — normal operation,
        must not raise."""
        pages = [ParsedPage(page_number=i, text=f"Acme Corp fact number {i} for FY2024.", tables=[]) for i in range(6)]
        # Fail batch 0 (both attempts — both calls consumed by page 0's
        # retry), succeed on every other batch. Responses must match each
        # batch's actual page content, page-number digit included, or the
        # numeric-verbatim guard correctly rejects them as fabricated.
        client = _FailThenSucceedLLMClient(
            fail_count=2,
            responses=[
                {"reasoning": "...", "propositions": [{"text": f"Acme Corp fact number {i} for FY2024.", "source_offset_hint": None}]}
                for i in range(1, 6)
            ],
        )

        result = segment_pages(pages, {}, llm_client=client, batch_size=1, max_attempts=2)

        assert len(result) == 5  # batches 1-5 succeeded; batch 0 was dropped, not fatal

    def test_many_consecutive_empty_batches_trips_the_breaker(self):
        """A systemic issue (bad credentials, persistent rate limiting) — a
        long run of consecutive empty batches must fail loudly rather than
        silently produce a near-empty document."""
        pages = [ParsedPage(page_number=i, text=f"Acme Corp fact number {i} for FY2024.", tables=[]) for i in range(6)]
        client = _AlwaysFailsLLMClient()

        with pytest.raises(TooManyConsecutiveBatchFailures):
            segment_pages(pages, {}, llm_client=client, batch_size=1, max_attempts=1, max_consecutive_batch_failures=5)

    def test_breaker_threshold_is_configurable(self):
        pages = [ParsedPage(page_number=i, text=f"Acme Corp fact number {i} for FY2024.", tables=[]) for i in range(3)]
        client = _AlwaysFailsLLMClient()

        with pytest.raises(TooManyConsecutiveBatchFailures):
            segment_pages(pages, {}, llm_client=client, batch_size=1, max_attempts=1, max_consecutive_batch_failures=2)

        assert client.calls == 2  # stopped as soon as the lowered threshold was hit


class _KeyedFakeLLMClient:
    """Looks up its response by a substring of `source_span` rather than
    popping off a queue — safe under concurrent, out-of-order calls, unlike
    `_FakeLLMClient`'s ordered queue. Thread-safe: only reads its (frozen at
    construction time) `_responses_by_key` dict."""

    def __init__(self, responses_by_key: dict[str, dict]):
        self._responses_by_key = responses_by_key
        self._lock = threading.Lock()
        self.calls = 0

    def complete(self, payload):
        with self._lock:
            self.calls += 1
        source_span = payload["source_span"]
        for key, response in self._responses_by_key.items():
            if key in source_span:
                return response
        raise AssertionError(f"no fake response registered for source_span: {source_span!r}")


class _ConcurrencyTrackingLLMClient:
    """Sleeps briefly inside `complete` and tracks the maximum number of
    calls observed in flight simultaneously — proves `segment_pages`'s
    `max_workers` path genuinely overlaps calls rather than serializing
    them under a lock somewhere."""

    def __init__(self, delay_seconds: float = 0.05):
        self._delay_seconds = delay_seconds
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight_observed = 0
        self.calls = 0

    def complete(self, payload):
        with self._lock:
            self._in_flight += 1
            self.max_in_flight_observed = max(self.max_in_flight_observed, self._in_flight)
            self.calls += 1
        time.sleep(self._delay_seconds)
        with self._lock:
            self._in_flight -= 1
        return {"reasoning": "...", "propositions": []}


class _InjectsInfrastructureErrorLLMClient:
    """Raises LLMInfrastructureError on every call — simulates a quota/auth/
    connection failure. Unlike LLMCallFailed, this must NOT be treated as
    "empty response, retry then drop" -- it must propagate out of
    segment_propositions and segment_pages uncaught."""

    def __init__(self):
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        raise LLMInfrastructureError("simulated insufficient_quota failure")


class TestInfrastructureFailuresHaltRatherThanDegrade:
    """Regression, 2026-07-25: a real insufficient_quota 429 during the
    proposition-sample diagnostic was silently degraded to "this page
    produced zero propositions" by the old undifferentiated LLMCallFailed
    handling. A quota/auth/connection failure must halt the run instead —
    see LLMInfrastructureError's docstring (llm/adapters/base.py) for why a
    mid-run quota drain would otherwise write empty propositions for every
    remaining page and produce a partial index that looks complete."""

    def test_segment_propositions_does_not_catch_infrastructure_errors(self):
        client = _InjectsInfrastructureErrorLLMClient()

        with pytest.raises(LLMInfrastructureError):
            segment_propositions("Acme Corp net sales were $32.2 billion.", {}, llm_client=client, max_attempts=2)

        assert client.calls == 1  # no retry -- an infrastructure failure isn't a per-span problem to retry

    def test_segment_pages_halts_on_the_first_page_rather_than_producing_a_partial_document(self):
        """The exact failure shape this guards against: a quota drain
        mid-document must not silently continue through the remaining pages
        producing an empty (but structurally valid-looking) result."""
        pages = [ParsedPage(page_number=i, text=f"Acme Corp fact number {i} for FY2024.", tables=[]) for i in range(10)]
        client = _InjectsInfrastructureErrorLLMClient()

        with pytest.raises(LLMInfrastructureError):
            segment_pages(pages, {}, llm_client=client, batch_size=1, max_attempts=1)

        # Halted on the very first batch -- never touched pages 2-10.
        assert client.calls == 1

    def test_segment_pages_halts_even_under_concurrency(self):
        pages = [ParsedPage(page_number=i, text=f"Acme Corp fact number {i} for FY2024.", tables=[]) for i in range(10)]
        client = _InjectsInfrastructureErrorLLMClient()

        with pytest.raises(LLMInfrastructureError):
            segment_pages(pages, {}, llm_client=client, batch_size=1, max_attempts=1, max_workers=4)


class TestSegmentPagesConcurrency:
    """`max_workers>1` — real thread-pool dispatch (2026-07-25), wired
    ahead of the metric-axis 5-document run to cut wall-clock below the
    ~80-90 min/document the sequential pilot observed."""

    def test_concurrent_execution_actually_overlaps_calls(self):
        """The point of max_workers>1: multiple calls genuinely in flight
        at once, not just dispatched-then-serialized somewhere internally."""
        pages = [ParsedPage(page_number=i, text=f"Fact number {i}.", tables=[]) for i in range(8)]
        client = _ConcurrencyTrackingLLMClient(delay_seconds=0.05)

        segment_pages(
            pages, {}, llm_client=client, batch_size=1, max_attempts=1,
            max_workers=4, max_consecutive_batch_failures=100,
        )

        assert client.calls == 8
        assert client.max_in_flight_observed > 1, (
            "expected genuine overlap with max_workers=4, but calls never overlapped — "
            "the concurrent path is not actually parallel"
        )

    def test_concurrent_results_match_sequential_results(self):
        """Same input, same fake responses, only max_workers differs —
        output (text + page attribution) must be identical and in the same
        original-batch order regardless of which call happens to finish
        first under concurrency."""
        pages = [
            ParsedPage(page_number=0, text="Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", tables=[]),
            ParsedPage(page_number=1, text="Acme Corp net sales for Q3 FY2024 were $8.5 billion.", tables=[]),
            ParsedPage(page_number=2, text="Acme Corp headcount for FY2024 was 4,200 employees.", tables=[]),
        ]
        responses_by_key = {
            "18.2 percent": {
                "reasoning": "...",
                "propositions": [{"text": "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.", "source_offset_hint": None}],
            },
            "$8.5 billion": {
                "reasoning": "...",
                "propositions": [{"text": "Acme Corp net sales for Q3 FY2024 were $8.5 billion.", "source_offset_hint": None}],
            },
            "4,200 employees": {
                "reasoning": "...",
                "propositions": [{"text": "Acme Corp headcount for FY2024 was 4,200 employees.", "source_offset_hint": None}],
            },
        }

        sequential_result = segment_pages(
            pages, {}, llm_client=_KeyedFakeLLMClient(responses_by_key), batch_size=1, max_workers=1
        )
        concurrent_result = segment_pages(
            pages, {}, llm_client=_KeyedFakeLLMClient(responses_by_key), batch_size=1, max_workers=4
        )

        sequential_view = [(p.text, p.page_number) for p in sequential_result]
        concurrent_view = [(p.text, p.page_number) for p in concurrent_result]
        assert concurrent_view == sequential_view

    def test_concurrent_circuit_breaker_still_trips_on_systemic_failure(self):
        pages = [ParsedPage(page_number=i, text=f"Acme Corp fact number {i} for FY2024.", tables=[]) for i in range(10)]
        client = _AlwaysFailsLLMClient()

        with pytest.raises(TooManyConsecutiveBatchFailures):
            segment_pages(
                pages, {}, llm_client=client, batch_size=1, max_attempts=1,
                max_consecutive_batch_failures=5, max_workers=4,
            )


class TestTableRowCompleteness:
    """`_table_rows_complete` -- see offline/propositions/segmenter_llm.py's
    module docstring for the real fbc_metric_pri_0044/0050/0052/etc.
    extraction bug this catches."""

    def test_row_with_no_label_embedded_number_is_not_flagged(self):
        from offline.propositions.segmenter_llm import SegmentedProposition, _table_rows_complete

        pages = [ParsedPage(page_number=0, text="", tables=[(("Total revenue", "34,229", "35,355"),)])]
        propositions = [SegmentedProposition(text="Something unrelated.", source_offset_hint=None)]

        assert _table_rows_complete(pages, propositions) is True

    def test_complete_when_a_related_proposition_states_the_real_value(self):
        from offline.propositions.segmenter_llm import SegmentedProposition, _table_rows_complete

        pages = [ParsedPage(
            page_number=0, text="",
            tables=[(("Accounts receivable - net of allowances of $174 and $189", "4,532", "4,660"),)],
        )]
        propositions = [
            SegmentedProposition(
                text="Acme Corp had accounts receivable of $4,532 million at December 31, 2022.",
                source_offset_hint=None,
            )
        ]

        assert _table_rows_complete(pages, propositions) is True

    def test_incomplete_when_only_the_label_embedded_decoy_is_stated(self):
        """The exact real bug: a proposition about the row exists, but only
        restates the allowance figure embedded in the label, never the
        row's own value column."""
        from offline.propositions.segmenter_llm import SegmentedProposition, _table_rows_complete

        pages = [ParsedPage(
            page_number=0, text="",
            tables=[(("Accounts receivable - net of allowances of $174 and $189", "4,532", "4,660"),)],
        )]
        propositions = [
            SegmentedProposition(
                text="Acme Corp had accounts receivable - net of allowances of $174 million at December 31, 2022.",
                source_offset_hint=None,
            )
        ]

        assert _table_rows_complete(pages, propositions) is False

    def test_row_never_mentioned_at_all_is_not_this_guards_concern(self):
        """A row dropped entirely (no proposition about it at all) is an
        ordinary drop, already handled elsewhere -- not what this guard
        checks for."""
        from offline.propositions.segmenter_llm import SegmentedProposition, _table_rows_complete

        pages = [ParsedPage(
            page_number=0, text="",
            tables=[(("Accounts receivable - net of allowances of $174 and $189", "4,532", "4,660"),)],
        )]
        propositions = [SegmentedProposition(text="3M Company had total revenue of $34,229 million.", source_offset_hint=None)]

        assert _table_rows_complete(pages, propositions) is True

    def test_no_tables_is_trivially_complete(self):
        from offline.propositions.segmenter_llm import SegmentedProposition, _table_rows_complete

        pages = [ParsedPage(page_number=0, text="Acme Corp net sales were $8.5 billion.", tables=[])]
        propositions = [SegmentedProposition(text="Acme Corp net sales were $8.5 billion.", source_offset_hint=None)]

        assert _table_rows_complete(pages, propositions) is True


class TestEscalation:
    """Escalating a single incomplete batch to a stronger model
    (`escalation_llm_client`), added 2026-07-26."""

    def test_incomplete_primary_result_triggers_escalation(self):
        pages = [ParsedPage(
            page_number=0,
            text="Accounts receivable - net of allowances of $174 and $189   4,532   4,660   December 31, 2022   December 31, 2021",
            tables=[(("Accounts receivable - net of allowances of $174 and $189", "4,532", "4,660"),)],
        )]
        primary = _FakeLLMClient(
            [{"reasoning": "...", "propositions": [
                {"text": "Acme Corp had accounts receivable - net of allowances of $174 million.", "source_offset_hint": None},
            ]}]
        )
        escalation = _FakeLLMClient(
            [{"reasoning": "...", "propositions": [
                {"text": "Acme Corp had accounts receivable of $4,532 million at December 31, 2022.", "source_offset_hint": None},
            ]}]
        )

        result = segment_pages(pages, {}, llm_client=primary, batch_size=1, escalation_llm_client=escalation)

        assert len(primary.calls) == 1
        assert len(escalation.calls) == 1  # escalation fired exactly once
        assert result[0].text == "Acme Corp had accounts receivable of $4,532 million at December 31, 2022."

    def test_complete_primary_result_never_escalates(self):
        pages = [ParsedPage(
            page_number=0,
            text="Accounts receivable - net of allowances of $174 and $189   4,532   4,660   December 31, 2022   December 31, 2021",
            tables=[(("Accounts receivable - net of allowances of $174 and $189", "4,532", "4,660"),)],
        )]
        primary = _FakeLLMClient(
            [{"reasoning": "...", "propositions": [
                {"text": "Acme Corp had accounts receivable of $4,532 million at December 31, 2022.", "source_offset_hint": None},
            ]}]
        )
        escalation = _FakeLLMClient([])  # would raise IndexError if ever called

        result = segment_pages(pages, {}, llm_client=primary, batch_size=1, escalation_llm_client=escalation)

        assert len(escalation.calls) == 0
        assert result[0].text == "Acme Corp had accounts receivable of $4,532 million at December 31, 2022."

    def test_no_escalation_client_configured_leaves_incomplete_result_as_is(self):
        """`escalation_llm_client=None` (the default) must behave exactly
        like before this feature existed -- no escalation attempted."""
        pages = [ParsedPage(
            page_number=0,
            text="Accounts receivable - net of allowances of $174 and $189   4,532   4,660   December 31, 2022   December 31, 2021",
            tables=[(("Accounts receivable - net of allowances of $174 and $189", "4,532", "4,660"),)],
        )]
        primary = _FakeLLMClient(
            [{"reasoning": "...", "propositions": [
                {"text": "Acme Corp had accounts receivable - net of allowances of $174 million.", "source_offset_hint": None},
            ]}]
        )

        result = segment_pages(pages, {}, llm_client=primary, batch_size=1)

        assert result[0].text == "Acme Corp had accounts receivable - net of allowances of $174 million."


class TestPerBatchCheckpointingAndDiagnostics:
    """Added 2026-07-26 after a real production run hit an unexplained
    LLMInfrastructureError 4 consecutive times on the same document, always
    losing the whole document's progress on retry with no visibility into
    which batch was in flight."""

    def test_on_batch_start_fires_before_each_batch_with_page_number(self):
        pages = [
            ParsedPage(page_number=10, text="Acme Corp fact one about FY2024.", tables=[]),
            ParsedPage(page_number=11, text="Acme Corp fact two about FY2024.", tables=[]),
        ]
        client = _FakeLLMClient(
            [
                {"reasoning": "...", "propositions": [{"text": "Acme Corp fact one about FY2024.", "source_offset_hint": None}]},
                {"reasoning": "...", "propositions": [{"text": "Acme Corp fact two about FY2024.", "source_offset_hint": None}]},
            ]
        )
        starts = []

        segment_pages(pages, {}, llm_client=client, batch_size=1, on_batch_start=lambda i, total, page: starts.append((i, total, page)))

        assert starts == [(0, 2, 10), (1, 2, 11)]

    def test_on_batch_complete_fires_after_each_batch_with_results(self):
        pages = [ParsedPage(page_number=0, text="Acme Corp fact about FY2024.", tables=[])]
        client = _FakeLLMClient(
            [{"reasoning": "...", "propositions": [{"text": "Acme Corp fact about FY2024.", "source_offset_hint": None}]}]
        )
        completions = []

        segment_pages(pages, {}, llm_client=client, batch_size=1, on_batch_complete=lambda i, results: completions.append((i, [r.text for r in results])))

        assert completions == [(0, ["Acme Corp fact about FY2024."])]

    def test_completed_batches_are_not_recalled_and_their_results_are_reused(self):
        """The core resumability guarantee: a batch already completed in a
        prior attempt must never be re-sent to the LLM, and its cached
        result must still appear in the final output."""
        pages = [
            ParsedPage(page_number=0, text="Acme Corp fact one about FY2024.", tables=[]),
            ParsedPage(page_number=1, text="Acme Corp fact two about FY2024.", tables=[]),
        ]
        # Only one response queued -- if batch 0 were re-called, this would
        # raise IndexError (no response left for it).
        client = _FakeLLMClient(
            [{"reasoning": "...", "propositions": [{"text": "Acme Corp fact two about FY2024.", "source_offset_hint": None}]}]
        )
        cached_batch_0 = [SegmentedProposition(text="Acme Corp fact one about FY2024.", source_offset_hint=None, page_number=0)]
        starts = []

        result = segment_pages(
            pages, {}, llm_client=client, batch_size=1,
            completed_batches={0: cached_batch_0},
            on_batch_start=lambda i, total, page: starts.append(i),
        )

        assert len(client.calls) == 1  # only batch 1 called the LLM
        assert starts == [1]  # batch 0 never fired on_batch_start -- it was never "started"
        texts = [p.text for p in result]
        assert "Acme Corp fact one about FY2024." in texts
        assert "Acme Corp fact two about FY2024." in texts

    def test_completed_batches_work_under_concurrency_too(self):
        pages = [
            ParsedPage(page_number=0, text="Acme Corp fact one about FY2024.", tables=[]),
            ParsedPage(page_number=1, text="Acme Corp fact two about FY2024.", tables=[]),
            ParsedPage(page_number=2, text="Acme Corp fact three about FY2024.", tables=[]),
        ]
        client = _KeyedFakeLLMClient(
            {
                "fact two": {"reasoning": "...", "propositions": [{"text": "Acme Corp fact two about FY2024.", "source_offset_hint": None}]},
                "fact three": {"reasoning": "...", "propositions": [{"text": "Acme Corp fact three about FY2024.", "source_offset_hint": None}]},
            }
        )
        cached_batch_0 = [SegmentedProposition(text="Acme Corp fact one about FY2024.", source_offset_hint=None, page_number=0)]

        result = segment_pages(
            pages, {}, llm_client=client, batch_size=1, max_workers=3,
            completed_batches={0: cached_batch_0},
        )

        texts = {p.text for p in result}
        assert texts == {
            "Acme Corp fact one about FY2024.",
            "Acme Corp fact two about FY2024.",
            "Acme Corp fact three about FY2024.",
        }

    def test_a_crash_after_the_last_logged_batch_start_identifies_the_culprit(self):
        """Simulates the real incident this feature exists for: batch 1
        raises an infrastructure error. on_batch_start's log must show
        batch 0 completed and batch 1 started -- exactly the diagnostic
        the real run lacked."""
        from llm.adapters.base import LLMInfrastructureError

        pages = [
            ParsedPage(page_number=0, text="Acme Corp fact one about FY2024.", tables=[]),
            ParsedPage(page_number=1, text="Acme Corp fact two about FY2024.", tables=[]),
        ]

        class _CrashesOnSecondCall:
            def __init__(self):
                self.calls = 0

            def complete(self, payload):
                self.calls += 1
                if self.calls == 2:
                    raise LLMInfrastructureError("simulated connection error")
                return {"reasoning": "...", "propositions": [{"text": "Acme Corp fact one about FY2024.", "source_offset_hint": None}]}

        starts = []
        with pytest.raises(LLMInfrastructureError):
            segment_pages(
                pages, {}, llm_client=_CrashesOnSecondCall(), batch_size=1,
                on_batch_start=lambda i, total, page: starts.append(i),
            )

        assert starts == [0, 1]  # batch 1 is the one that was in flight when it crashed
