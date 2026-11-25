"""C4: full framework, hybrid + metadata filter + rerank + NPCE + CoVe.
Same fan-out and draft assembler as C3; only the verifier differs (C2 to C1's relationship).

Abstention short-circuits like C2, generalized to "nothing retrieved
anywhere / no sub-query answerable" across the whole fan-out.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from config.schema import ExperimentConfig
from contracts.interfaces import Embedder, Reranker
from contracts.models import PlaceholderRegistryEntry, Proposition, RequestContext, VerificationClaimResult
from llm.client import LLMClient
from online.analysis.query_splitter import DictionaryLookup, LLMQuerySplitter, UnmatchedLogger, render_subquery_question
from offline.labeling.metric_labeler import label_proposition_if_needed
from online.curation.fff_rewriter import rewrite_to_ffts
from online.curation.near_identical import detect_near_identical_versions, answer_each_version
from online.generation.assembler import SubAnswer, assemble_draft
from online.output.abstention_gate import decide
from online.output.citation_binder import bind_citations
from online.output.confidence_score import compute as compute_confidence
from online.output.generator import AnswerValue, GeneratedAnswer, generate_answer
from online.placeholders.remapper import remap_entity, reverse_map
from online.routing.question_fixer import fix_question
from online.routing.retry_with_other_names import filter_with_retry
from online.routing.router import cap_pieces, choose_road
from online.routing.step_planner import plan_steps, substitute_step_answers
from online.retrieval.ask_match import drop_clear_mismatches
from online.retrieval.fusion import apply_time_weighting, reciprocal_rank_fusion
from online.retrieval.hybrid import TooManyVectorsMissing
from online.retrieval.hybrid import retrieve as hybrid_retrieve
from online.verification.cove_orchestrator import CoVeVerifier

# Decision 26: defensive-only cap on on-demand labeling model calls per
# sub-query. `reranked_props` is already bounded by `rerank_top_k` (<=10 in
# every real config today), so this is not the binding constraint in
# normal operation -- it only guards against some future config setting
# `rerank_top_k` unusually high. Same number decision 25 used, kept for
# continuity, not re-derived from anything about this call site.
MAX_ONDEMAND_LABEL_CALLS_PER_SUBQUERY = 20

# Decision 38: the per-sub-query loop and the per-sub-answer CoVe loop
# below both run on thread pools now (same reasoning for both -- nothing
# inside one iteration reads another's just-computed result, see the
# comments at each call site). Capped at decision 11's own sub-query limit
# (6): no benefit spinning up more workers than there is work to do, and
# this keeps the worst case bounded the same way every other cap in this
# file already is.
MAX_SUBQUERY_WORKERS = 6
MAX_COVE_WORKERS = 6


@dataclass(frozen=True)
class C4AnswerResult:
    question: str
    abstained: bool
    abstention_message: str
    answerable: bool
    answers: list[AnswerValue]
    answer_text: str
    citations: list[dict[str, Any]]
    used_fff_ids: list[str]
    sub_query_count: int
    fff_count: int
    query_splitter_model: str
    fff_rewriter_model: str
    generator_model: str
    cove_verification_model: str
    verification_trace: list[dict[str, Any]]
    confidence_score: float = 0.0
    # Step 4.1: which road the router took, and why -- the data "How it
    # happened" (step 4.7) will surface. Not yet a rewritten question or a
    # multi-hop plan; see online/routing/router.py's own scope note.
    routing_road: str = "straight"
    routing_reason: str = ""
    routing_capped: bool = False
    # Step 4.4: see online/pipeline_c3.py's identical fields for the note.
    question_fix_outcome: str = "unchanged"
    question_fixes: tuple[tuple[str, str], ...] = ()
    # Step 4.5: whether any sub-query needed a retry with another known
    # name for the company, and which name(s) it tried.
    retry_attempted: bool = False
    retry_names_tried: tuple[str, ...] = ()
    # Step 4.6: one row per step actually run on the step-by-step road
    # (empty on every other road) -- "How it happened" (step 4.7) shows
    # these; a plan capped at MAX_STEPS is noted via routing_capped.
    step_plan: tuple[dict[str, Any], ...] = ()
    # Step 4.7: the question actually used after any fix (step 4.4), and
    # the real-name pieces it was split or stepped into, if any -- the raw
    # data "How it happened" is built from.
    question_understood: str = ""
    pieces: tuple[str, ...] = ()
    # Decision 11's "several versions" case (step 4.1's own test row, built
    # 2026-09-27): whether the top evidence for a sub-query had two
    # genuinely different values for the same period, and what differed
    # (always "basis" today -- reported vs restated). See
    # online/curation/near_identical.py.
    near_identical_detected: bool = False
    near_identical_detail: str = ""


@dataclass
class _SubQueryOutcome:
    """One sub-query's worth of loop-1 work (decision 38). Every write the
    old sequential loop used to make directly -- `pair_facts`, and the
    running `all_ffts`/`all_reranked_props`/`retry_*`/`near_identical_*`
    state -- is returned here instead, so the calling thread can apply all
    of it back in sub-query order (never completion order) once every
    worker is done, not while any of them are still running."""

    rendered_question: str
    retry_attempted: bool = False
    retry_names_tried: tuple[str, ...] = ()
    sub_answers: list[SubAnswer] = field(default_factory=list)
    sub_answer_ffts: list[list] = field(default_factory=list)
    fff_contributions: list = field(default_factory=list)
    reranked_props: dict[str, Proposition] = field(default_factory=dict)
    # (sub_query_id, ffts) pairs -- equivalent to a `pair_facts` call per
    # pair, applied by the caller instead of here.
    fff_set_updates: list[tuple[str, list]] = field(default_factory=list)
    near_identical_detected: bool = False
    near_identical_detail: str = ""


def _answer_one_sub_query(
    sub_query: Any,
    ctx: RequestContext,
    bundle: Any,
    embedder: Embedder,
    reranker: Reranker,
    fff_client: LLMClient,
    generator_client: LLMClient,
    config: ExperimentConfig,
    registry: Sequence[PlaceholderRegistryEntry],
    top_k: int,
    rerank_top_k: int,
    metric_label_llm_client: Optional[LLMClient],
) -> _SubQueryOutcome:
    """One sub-query's retrieval -> rerank -> (near-identical or normal)
    FFF-rewrite + generation. Pulled out of `answer_question`'s loop so it
    can run on its own thread (decision 38).

    Only ever READS `ctx` here (`filter_with_retry`/`resolve_company`
    read `ctx.placeholder_map`; `reranker.rerank` does too, for query-text
    rendering). Every placeholder this run will ever need was already
    written to `ctx.placeholder_map` by `remap_entity`, sequentially,
    *before* any of these threads start (see the list comprehension right
    before the thread pool in `answer_question` below) -- so there is no
    write to race here, and no lock is needed for this function's own body.
    """
    rendered_question = render_subquery_question(sub_query)

    retry_outcome = filter_with_retry(
        sub_query, ctx, bundle.metadata_store, registry,
        enable_metric_filter=config.retrieval.enable_metric_filter,
    )
    retry_attempted = retry_outcome.retried
    retry_names_tried = tuple(retry_outcome.names_tried)
    candidates = retry_outcome.candidates

    try:
        scored = hybrid_retrieve(rendered_question, candidates, embedder, dense_index=bundle.dense_index)
    except TooManyVectorsMissing:
        # The index needs repair (step 1.3 fixes it in the background) --
        # this one sub-query goes unanswered rather than the whole question crashing.
        return _SubQueryOutcome(
            rendered_question=rendered_question,
            retry_attempted=retry_attempted,
            retry_names_tried=retry_names_tried,
            sub_answers=[SubAnswer(sub_query=sub_query, generated=GeneratedAnswer(answerable=False))],
            sub_answer_ffts=[[]],
        )

    fused = reciprocal_rank_fusion(scored)
    fused = apply_time_weighting(fused, enabled=config.retrieval.time_weighting)
    fused_props: list[Proposition] = [f.proposition for f in fused]

    tie_margin = config.retrieval.near_identical_logit_margin
    rerank_logits: dict[str, float] = {}
    if config.retrieval.enable_reranker and fused_props and tie_margin is not None and hasattr(reranker, "rerank_scored"):
        scored_pairs = reranker.rerank_scored(sub_query, fused_props[:top_k], k=rerank_top_k, ctx=ctx)
        reranked_props = [p for p, _ in scored_pairs]
        rerank_logits = {p.proposition_id: logit for p, logit in scored_pairs}
    elif config.retrieval.enable_reranker and fused_props:
        reranked_props = list(reranker.rerank(sub_query, fused_props[:top_k], k=rerank_top_k, ctx=ctx))
    else:
        reranked_props = fused_props[:rerank_top_k]

    if config.retrieval.enable_ondemand_metric_labeling and metric_label_llm_client is not None:
        attempts_made = 0
        fresh_reranked_props = []
        for p in reranked_props:
            # Every unlabelled proposition reaching the helper can cost a
            # real model call, so the cap counts attempts, not just the
            # ones that actually changed a label.
            if p.metrics or attempts_made >= MAX_ONDEMAND_LABEL_CALLS_PER_SUBQUERY:
                fresh_reranked_props.append(p)
                continue
            attempts_made += 1
            fresh_reranked_props.append(
                label_proposition_if_needed(p, bundle.metadata_store, llm_client=metric_label_llm_client)
            )
        reranked_props = fresh_reranked_props

    if config.retrieval.enable_ask_match_guard:
        # Runs after labeling on purpose: fresh labels make the kind check sharper.
        reranked_props = drop_clear_mismatches(sub_query, reranked_props)

    reranked_props_by_id = {p.proposition_id: p for p in reranked_props}

    # Step 4.1's own "several versions" case: two genuinely different
    # numbers for the same period -- answer each, labelled, instead of the
    # generator silently blending or preferring one.
    collision = detect_near_identical_versions(reranked_props, logits=rerank_logits or None, tie_margin=tie_margin)
    if collision is not None:
        sub_answers: list[SubAnswer] = []
        sub_answer_ffts: list[list] = []
        fff_contributions: list = []
        fff_set_updates: list[tuple[str, list]] = []
        for sub_answer, version_ffts in answer_each_version(
            sub_query, rendered_question, collision, fff_client, generator_client
        ):
            fff_set_updates.append((sub_answer.sub_query.sub_query_id, version_ffts))
            fff_contributions.extend(version_ffts)
            sub_answers.append(sub_answer)
            sub_answer_ffts.append(version_ffts)
        return _SubQueryOutcome(
            rendered_question=rendered_question,
            retry_attempted=retry_attempted,
            retry_names_tried=retry_names_tried,
            sub_answers=sub_answers,
            sub_answer_ffts=sub_answer_ffts,
            fff_contributions=fff_contributions,
            reranked_props=reranked_props_by_id,
            fff_set_updates=fff_set_updates,
            near_identical_detected=True,
            near_identical_detail=collision.differing_detail,
        )

    ffts = rewrite_to_ffts(rendered_question, reranked_props, fff_client, subject_override=sub_query.entity)
    generated = generate_answer(rendered_question, ffts, generator_client)
    return _SubQueryOutcome(
        rendered_question=rendered_question,
        retry_attempted=retry_attempted,
        retry_names_tried=retry_names_tried,
        sub_answers=[SubAnswer(sub_query=sub_query, generated=generated)],
        sub_answer_ffts=[ffts],
        fff_contributions=list(ffts),
        reranked_props=reranked_props_by_id,
        fff_set_updates=[(sub_query.sub_query_id, ffts)],
    )


def answer_question(
    question: str,
    bundle: Any,  # offline.indexing.build.IndexBundle -- duck-typed, see evaluation.run_retrieval_gate
    embedder: Embedder,
    reranker: Reranker,
    splitter_client: LLMClient,
    fff_client: LLMClient,
    generator_client: LLMClient,
    cove_claim_extraction_client: LLMClient,
    cove_independent_verification_client: LLMClient,
    cove_prose_comparison_client: LLMClient,
    config: ExperimentConfig,
    registry: Sequence[PlaceholderRegistryEntry],
    fff_rewriter_model: str,
    generator_model: str,
    query_splitter_model: str,
    cove_verification_model: str,
    dictionary_lookup: Optional[DictionaryLookup] = None,
    unmatched_logger: Optional[UnmatchedLogger] = None,
    step_planner_client: Optional[LLMClient] = None,
    metric_label_llm_client: Optional[LLMClient] = None,
) -> C4AnswerResult:
    """C4's query -> split -> NPCE -> answer -> CoVe-verified -> citation
    path. LLM calls happen inside the sub-functions; this is pure orchestration.

    `dictionary_lookup`/`unmatched_logger` (step 2.2) are optional -- leaving
    them out behaves exactly as before free-text metrics existed.

    `step_planner_client` (step 4.6) is optional too: omitting it means a
    question the router would send down the step-by-step road is instead
    answered the normal (straight/split) way, the same safe default every
    other optional call site in this project falls back to. Never passed
    on a step's own recursive call to this function, by construction --
    one level of step-by-step only, never a plan of plans.

    `metric_label_llm_client` (decision 25) is optional too, and is only
    actually used when `config.retrieval.enable_ondemand_metric_labeling`
    is also true -- a client being configured is not by itself enough to
    turn this on, so an experiment cell that doesn't opt in sees zero
    change even if a client happens to be passed."""
    ctx = RequestContext(request_id="c4", config_hash=config.experiment_id)
    top_k = config.retrieval.top_k
    rerank_top_k = config.retrieval.rerank_top_k

    # Step 4.4: fix a typo, or ask instead of guessing when a pronoun has no
    # named company to point at -- before routing or splitting sees it.
    question_fix = fix_question(question, [entry.surface_form for entry in registry])
    if question_fix.outcome == "needs_clarification":
        return C4AnswerResult(
            question=question,
            abstained=True,
            abstention_message=question_fix.clarification_message or "",
            answerable=False,
            answers=[],
            answer_text="",
            citations=[],
            used_fff_ids=[],
            sub_query_count=0,
            fff_count=0,
            query_splitter_model=query_splitter_model,
            fff_rewriter_model=fff_rewriter_model,
            generator_model=generator_model,
            cove_verification_model=cove_verification_model,
            verification_trace=[],
            question_fix_outcome=question_fix.outcome,
            question_understood=question,
        )
    working_question = question_fix.question

    routing_decision = choose_road(working_question)

    # Step 4.6: the one road that genuinely changes control flow -- straight
    # and split_money still just get logged, the actual splitting is the
    # query splitter's own job either way (see the comment this replaced).
    if routing_decision.road == "step_by_step" and step_planner_client is not None:
        plan = plan_steps(working_question, step_planner_client)
        if plan is not None:
            answers_by_step_id: dict[int, str] = {}
            step_log: list[dict[str, Any]] = []
            last_step_result: Optional[C4AnswerResult] = None
            for step in plan.steps:
                step_question = substitute_step_answers(step.question, answers_by_step_id)
                last_step_result = answer_question(
                    step_question, bundle, embedder, reranker,
                    splitter_client, fff_client, generator_client,
                    cove_claim_extraction_client, cove_independent_verification_client, cove_prose_comparison_client,
                    config, registry=registry,
                    fff_rewriter_model=fff_rewriter_model, generator_model=generator_model,
                    query_splitter_model=query_splitter_model, cove_verification_model=cove_verification_model,
                    dictionary_lookup=dictionary_lookup, unmatched_logger=unmatched_logger,
                    step_planner_client=None,  # one level of step-by-step only, never a plan of plans
                    metric_label_llm_client=metric_label_llm_client,
                )
                succeeded = last_step_result.answerable and not last_step_result.abstained
                step_log.append({
                    "step_id": step.step_id, "question": step_question,
                    "answer_text": last_step_result.answer_text, "answerable": succeeded,
                })
                if not succeeded:
                    return C4AnswerResult(
                        question=question,
                        abstained=True,
                        abstention_message=f"Step {step.step_id} could not be answered: {step_question}",
                        answerable=False, answers=[], answer_text="", citations=[], used_fff_ids=[],
                        sub_query_count=step.step_id, fff_count=last_step_result.fff_count,
                        query_splitter_model=query_splitter_model, fff_rewriter_model=fff_rewriter_model,
                        generator_model=generator_model, cove_verification_model=cove_verification_model,
                        verification_trace=[],
                        routing_road=routing_decision.road, routing_reason=routing_decision.reason,
                        routing_capped=plan.capped,
                        question_fix_outcome=question_fix.outcome, question_fixes=tuple(question_fix.fixes),
                        step_plan=tuple(step_log),
                        question_understood=working_question,
                        pieces=tuple(s["question"] for s in step_log),
                    )
                answers_by_step_id[step.step_id] = last_step_result.answer_text

            assert last_step_result is not None  # plan.steps is never empty (validate_step_plan rejects that)
            return C4AnswerResult(
                question=question,
                abstained=last_step_result.abstained,
                abstention_message=last_step_result.abstention_message,
                answerable=last_step_result.answerable,
                answers=last_step_result.answers,
                answer_text=last_step_result.answer_text,
                citations=last_step_result.citations,
                used_fff_ids=last_step_result.used_fff_ids,
                sub_query_count=len(plan.steps),
                fff_count=last_step_result.fff_count,
                query_splitter_model=query_splitter_model, fff_rewriter_model=fff_rewriter_model,
                generator_model=generator_model, cove_verification_model=cove_verification_model,
                verification_trace=last_step_result.verification_trace,
                confidence_score=last_step_result.confidence_score,
                routing_road=routing_decision.road, routing_reason=routing_decision.reason,
                routing_capped=plan.capped,
                question_fix_outcome=question_fix.outcome, question_fixes=tuple(question_fix.fixes),
                step_plan=tuple(step_log),
                question_understood=working_question,
                pieces=tuple(s["question"] for s in step_log),
            )
        # plan rejected (empty, a loop, or unparseable) -- fall through to
        # the normal splitter path below, the same safe default every other
        # optional fallback in this project uses when a model's output
        # can't be trusted.

    raw_sub_queries = LLMQuerySplitter(
        llm_client=splitter_client, dictionary_lookup=dictionary_lookup, unmatched_logger=unmatched_logger
    ).transform(working_question, ctx)
    remapped_sub_queries = [
        sq.model_copy(update={"entity": remap_entity(sq.entity, ctx, registry=registry)})
        for sq in raw_sub_queries
    ]
    # Decision 11's guard rail: at most 6 pieces, whatever the split produced.
    sub_queries, routing_capped = cap_pieces(remapped_sub_queries)

    all_ffts = []
    all_reranked_props: dict[str, Proposition] = {}
    sub_answers: list[SubAnswer] = []
    # Parallel to `sub_answers`, one entry per sub-answer -- which FFFs
    # back it, so fix 1's narrower verification evidence can be built
    # without re-deriving it from `all_ffts`.
    sub_answer_ffts: list[list] = []
    retry_attempted = False
    retry_names_tried: list[str] = []
    rendered_questions: list[str] = []
    near_identical_detected = False
    near_identical_detail = ""

    # Decision 38: each sub-query's metadata filtering, retrieval, rerank,
    # on-demand labeling, near-identical check, FFF rewrite, and generation
    # are independent of every other sub-query's (nothing here reads
    # another iteration's just-computed result -- confirmed by reading
    # every function this loop calls), so they run on their own threads
    # instead of one full sub-query at a time. Same total LLM calls, same
    # total cost, just overlapped instead of queued.
    #
    # `ctx.placeholder_map` is read-only inside `_answer_one_sub_query` --
    # every write to it already happened above, sequentially, via
    # `remap_entity` on `raw_sub_queries`/`remapped_sub_queries`, before
    # this pool starts. So there is nothing to lock for that field. The
    # only other mutable state a sub-query's processing used to write
    # directly (`ctx.fff_sets`, via `pair_facts`) is returned from each
    # worker instead and applied below, in sub-query order, only after
    # every worker has finished.
    worker_count = max(1, min(len(sub_queries), MAX_SUBQUERY_WORKERS))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        outcomes = list(executor.map(
            lambda sq: _answer_one_sub_query(
                sq, ctx, bundle, embedder, reranker, fff_client, generator_client,
                config, registry, top_k, rerank_top_k, metric_label_llm_client,
            ),
            sub_queries,
        ))

    # `executor.map` yields results in the order its inputs were given
    # (sub-query order), never completion order, so merging in this order
    # reproduces exactly what the old sequential loop would have produced
    # for `rendered_questions`, `used_fff_ids`, `combined_answers`, and
    # `near_identical_detail` (last colliding sub-query in iteration order
    # wins, same as the old "overwrite unconditionally" sequential code).
    for outcome in outcomes:
        rendered_questions.append(outcome.rendered_question)
        if outcome.retry_attempted:
            retry_attempted = True
            retry_names_tried.extend(outcome.retry_names_tried)
        sub_answers.extend(outcome.sub_answers)
        sub_answer_ffts.extend(outcome.sub_answer_ffts)
        all_ffts.extend(outcome.fff_contributions)
        all_reranked_props.update(outcome.reranked_props)
        for sub_query_id, ffts in outcome.fff_set_updates:
            # Equivalent to `pair_facts(sub_query, ffts, ctx)` -- that
            # function just does this same assignment keyed by
            # `sub_query.sub_query_id`, which is all each worker returned.
            ctx.fff_sets[sub_query_id] = list(ffts)
        if outcome.near_identical_detected:
            near_identical_detected = True
            near_identical_detail = outcome.near_identical_detail

    draft = assemble_draft(sub_answers)
    any_answerable = any(sa.generated.answerable and sa.generated.answers for sa in sub_answers)

    # Decision 21 (fix 1): a sub-query that came back "not provided" (the
    # Cartesian product's own filler combos nobody asked about, same as a
    # genuine gap) still belongs in the user-visible `draft` above -- we
    # never hide a gap. But it must never reach CoVe's claim extraction,
    # because CoVe will dutifully extract a claim from "not provided" text
    # too, that claim comes back unsupported, and the abstention gate then
    # abstains the WHOLE answer even when every claim the user actually
    # asked about was retrieved and verified correctly. So CoVe gets a
    # second, narrower draft built from only the answerable sub-answers;
    # the final `answer_text` below is still built from ALL of them.
    answerable_sub_answers = [
        (sa, ffts) for sa, ffts in zip(sub_answers, sub_answer_ffts) if sa.generated.answerable and sa.generated.answers
    ]

    if not all_ffts or not any_answerable:
        trace: Sequence[VerificationClaimResult] = [
            VerificationClaimResult(claim_id="c_1", claim_text=question, is_numeric=True, verdict="unsupported")
        ]
        verified_answer_text = draft
    else:
        # One verify() call per answerable sub-answer, each seeing only its
        # own evidence -- a sub-answer never gets checked against a different sub-answer's facts.
        verifier = CoVeVerifier(
            cove_claim_extraction_client, cove_independent_verification_client, cove_prose_comparison_client
        )

        # Decision 38: each sub-answer's verify() call (claim extraction +
        # independent verification, 2+ LLM calls) is independent of every
        # other's -- `CoVeVerifier.verify` never reads or writes `ctx`
        # (confirmed in online/verification/cove_orchestrator.py: `ctx` is
        # accepted but unused), so these run concurrently too. `map`
        # returns `verified_list` in `answerable_sub_answers` order, never
        # completion order, and `combined_trace` is only ever built
        # afterwards, on this one thread -- so the claim_id renumbering
        # below is byte-identical to the old sequential loop regardless of
        # which sub-answer's verify() actually finished first.
        cove_worker_count = max(1, min(len(answerable_sub_answers), MAX_COVE_WORKERS))
        with ThreadPoolExecutor(max_workers=cove_worker_count) as executor:
            verified_list = list(executor.map(
                lambda item: verifier.verify(assemble_draft([item[0]]), evidence=item[1], ctx=ctx),
                answerable_sub_answers,
            ))

        combined_trace: list[VerificationClaimResult] = []
        for verified in verified_list:
            for claim in verified.verification_trace:
                # Give each merged claim a fresh id -- every sub-answer's own trace starts back at c_1.
                combined_trace.append(
                    claim.model_copy(update={"claim_id": f"c_{len(combined_trace) + 1}"})
                )
        trace = combined_trace
        # The visible text still comes from the full draft, gaps and all --
        # never from any one sub-answer's own echoed-back text.
        verified_answer_text = draft

    decision = decide(trace)
    confidence_score = compute_confidence(trace)
    final_text = reverse_map(verified_answer_text, ctx)

    fff_by_id = {f.fff_id: f for f in all_ffts}
    used_fff_ids = [fid for sa in sub_answers for fid in sa.generated.used_fff_ids]
    used_propositions = [
        all_reranked_props[fff_by_id[fid].proposition_id]
        for fid in used_fff_ids
        if fid in fff_by_id and fff_by_id[fid].proposition_id in all_reranked_props
    ]
    citations = bind_citations(final_text, used_propositions)

    combined_answers: list[AnswerValue] = [av for sa in sub_answers for av in sa.generated.answers]

    return C4AnswerResult(
        question=question,
        abstained=decision.abstained,
        abstention_message=decision.message,
        answerable=any_answerable,
        answers=combined_answers,
        answer_text=final_text,
        citations=citations,
        used_fff_ids=used_fff_ids,
        sub_query_count=len(sub_queries),
        fff_count=len(all_ffts),
        query_splitter_model=query_splitter_model,
        fff_rewriter_model=fff_rewriter_model,
        generator_model=generator_model,
        cove_verification_model=cove_verification_model,
        verification_trace=[
            {
                "claim_id": c.claim_id,
                "claim_text": c.claim_text,
                "is_numeric": c.is_numeric,
                "verdict": c.verdict,
                "supporting_fff_ids": list(c.supporting_fff_ids),
            }
            for c in trace
        ],
        confidence_score=confidence_score,
        routing_road=routing_decision.road,
        routing_reason=routing_decision.reason,
        routing_capped=routing_capped,
        question_fix_outcome=question_fix.outcome,
        question_fixes=tuple(question_fix.fixes),
        retry_attempted=retry_attempted,
        retry_names_tried=tuple(retry_names_tried),
        question_understood=working_question,
        pieces=tuple(reverse_map(q, ctx) for q in rendered_questions),
        near_identical_detected=near_identical_detected,
        near_identical_detail=near_identical_detail,
    )
