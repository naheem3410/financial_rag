"""C3: hybrid + metadata filter + rerank + NPCE, CoVe off. Mirrors C1's
shape but fans out the split-to-generate chain once per sub-query.

Per sub-query: placeholder remap -> metadata filter -> hybrid retrieve +
fusion -> rerank -> FFF rewrite (real company name kept out via
subject_override) -> pairer -> generator. Then: draft assembler ->
PassthroughVerifier -> reverse-map placeholders back -> citations ->
abstention (abstains only if no sub-query was answerable at all).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from config.schema import ExperimentConfig
from contracts.interfaces import Embedder, Reranker
from contracts.models import PlaceholderRegistryEntry, Proposition, RequestContext, VerificationClaimResult
from llm.client import LLMClient
from online.analysis.query_splitter import DictionaryLookup, LLMQuerySplitter, UnmatchedLogger, render_subquery_question
from online.curation.fff_rewriter import rewrite_to_ffts
from online.curation.near_identical import detect_near_identical_versions, answer_each_version
from online.curation.pairer import pair_facts
from online.generation.assembler import SubAnswer, assemble_draft
from online.output.abstention_gate import decide
from online.output.citation_binder import bind_citations
from online.output.generator import AnswerValue, GeneratedAnswer, generate_answer
from online.placeholders.remapper import remap_entity, reverse_map
from online.routing.question_fixer import fix_question
from online.routing.retry_with_other_names import filter_with_retry
from online.routing.router import cap_pieces, choose_road
from online.routing.step_planner import plan_steps, substitute_step_answers
from online.retrieval.fusion import apply_time_weighting, reciprocal_rank_fusion
from online.retrieval.hybrid import TooManyVectorsMissing
from online.retrieval.hybrid import retrieve as hybrid_retrieve
from online.verification.cove_orchestrator import PassthroughVerifier


@dataclass(frozen=True)
class C3AnswerResult:
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
    fff_rewriter_model: str
    generator_model: str
    query_splitter_model: str
    # Step 4.1: see online/pipeline_c4.py's identical fields for the note.
    routing_road: str = "straight"
    routing_reason: str = ""
    routing_capped: bool = False
    # Step 4.4: a typo fix applied before routing/splitting, or an honest
    # "which company do you mean?" when a pronoun has nothing to point at.
    question_fix_outcome: str = "unchanged"
    question_fixes: tuple[tuple[str, str], ...] = ()
    # Step 4.5: whether any sub-query needed a retry with another known
    # name for the company, and which name(s) it tried.
    retry_attempted: bool = False
    retry_names_tried: tuple[str, ...] = ()
    # Step 4.6: see online/pipeline_c4.py's identical field for the note.
    step_plan: tuple[dict[str, Any], ...] = ()
    # Step 4.7: see online/pipeline_c4.py's identical fields for the note.
    question_understood: str = ""
    pieces: tuple[str, ...] = ()
    # See online/pipeline_c4.py's identical fields for the note.
    near_identical_detected: bool = False
    near_identical_detail: str = ""


def answer_question(
    question: str,
    bundle: Any,  # offline.indexing.build.IndexBundle -- duck-typed, see evaluation.run_retrieval_gate
    embedder: Embedder,
    reranker: Reranker,
    splitter_client: LLMClient,
    fff_client: LLMClient,
    generator_client: LLMClient,
    config: ExperimentConfig,
    registry: Sequence[PlaceholderRegistryEntry],
    fff_rewriter_model: str,
    generator_model: str,
    query_splitter_model: str,
    dictionary_lookup: Optional[DictionaryLookup] = None,
    unmatched_logger: Optional[UnmatchedLogger] = None,
    step_planner_client: Optional[LLMClient] = None,
) -> C3AnswerResult:
    """C3's query -> answer -> citation path. LLM calls happen inside the
    splitter/rewriter/generator sub-functions; this is pure orchestration.

    `dictionary_lookup`/`unmatched_logger` (step 2.2) are optional -- leaving
    them out behaves exactly as before free-text metrics existed.
    `step_planner_client` (step 4.6): see online/pipeline_c4.py's identical
    parameter for the note -- optional, never passed on a step's own
    recursive call, one level of step-by-step only."""
    ctx = RequestContext(request_id="c3", config_hash=config.experiment_id)
    top_k = config.retrieval.top_k
    rerank_top_k = config.retrieval.rerank_top_k

    # Step 4.4: fix a typo, or ask instead of guessing when a pronoun has no
    # named company to point at -- before routing or splitting sees it.
    question_fix = fix_question(question, [entry.surface_form for entry in registry])
    if question_fix.outcome == "needs_clarification":
        return C3AnswerResult(
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
            fff_rewriter_model=fff_rewriter_model,
            generator_model=generator_model,
            query_splitter_model=query_splitter_model,
            question_fix_outcome=question_fix.outcome,
            question_understood=question,
        )
    working_question = question_fix.question

    routing_decision = choose_road(working_question)

    # Step 4.6: the one road that genuinely changes control flow -- straight
    # and split_money still just get logged, the actual splitting is the
    # query splitter's own job either way.
    if routing_decision.road == "step_by_step" and step_planner_client is not None:
        plan = plan_steps(working_question, step_planner_client)
        if plan is not None:
            answers_by_step_id: dict[int, str] = {}
            step_log: list[dict[str, Any]] = []
            last_step_result: Optional[C3AnswerResult] = None
            for step in plan.steps:
                step_question = substitute_step_answers(step.question, answers_by_step_id)
                last_step_result = answer_question(
                    step_question, bundle, embedder, reranker,
                    splitter_client, fff_client, generator_client,
                    config, registry=registry,
                    fff_rewriter_model=fff_rewriter_model, generator_model=generator_model,
                    query_splitter_model=query_splitter_model,
                    dictionary_lookup=dictionary_lookup, unmatched_logger=unmatched_logger,
                    step_planner_client=None,  # one level of step-by-step only, never a plan of plans
                )
                succeeded = last_step_result.answerable and not last_step_result.abstained
                step_log.append({
                    "step_id": step.step_id, "question": step_question,
                    "answer_text": last_step_result.answer_text, "answerable": succeeded,
                })
                if not succeeded:
                    return C3AnswerResult(
                        question=question,
                        abstained=True,
                        abstention_message=f"Step {step.step_id} could not be answered: {step_question}",
                        answerable=False, answers=[], answer_text="", citations=[], used_fff_ids=[],
                        sub_query_count=step.step_id, fff_count=last_step_result.fff_count,
                        fff_rewriter_model=fff_rewriter_model, generator_model=generator_model,
                        query_splitter_model=query_splitter_model,
                        routing_road=routing_decision.road, routing_reason=routing_decision.reason,
                        routing_capped=plan.capped,
                        question_fix_outcome=question_fix.outcome, question_fixes=tuple(question_fix.fixes),
                        step_plan=tuple(step_log),
                        question_understood=working_question,
                        pieces=tuple(s["question"] for s in step_log),
                    )
                answers_by_step_id[step.step_id] = last_step_result.answer_text

            assert last_step_result is not None  # plan.steps is never empty (validate_step_plan rejects that)
            return C3AnswerResult(
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
                fff_rewriter_model=fff_rewriter_model, generator_model=generator_model,
                query_splitter_model=query_splitter_model,
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
    # remap before the metadata filter or any further LLM call sees it
    remapped_sub_queries = [
        sq.model_copy(update={"entity": remap_entity(sq.entity, ctx, registry=registry)})
        for sq in raw_sub_queries
    ]
    # Decision 11's guard rail: at most 6 pieces, whatever the split produced.
    sub_queries, routing_capped = cap_pieces(remapped_sub_queries)

    all_ffts = []
    all_reranked_props: dict[str, Proposition] = {}
    sub_answers: list[SubAnswer] = []
    retry_attempted = False
    retry_names_tried: list[str] = []
    rendered_questions: list[str] = []
    near_identical_detected = False
    near_identical_detail = ""

    for sub_query in sub_queries:
        rendered_question = render_subquery_question(sub_query)
        rendered_questions.append(rendered_question)

        # Step 4.5: an empty result gets one retry with another known name
        # for the same company before this sub-query goes unanswered.
        retry_outcome = filter_with_retry(
            sub_query, ctx, bundle.metadata_store, registry, enable_metric_filter=config.retrieval.enable_metric_filter
        )
        if retry_outcome.retried:
            retry_attempted = True
            retry_names_tried.extend(retry_outcome.names_tried)
        candidates = retry_outcome.candidates
        try:
            scored = hybrid_retrieve(rendered_question, candidates, embedder, dense_index=bundle.dense_index)
        except TooManyVectorsMissing:
            sub_answers.append(SubAnswer(sub_query=sub_query, generated=GeneratedAnswer(answerable=False)))
            continue
        fused = reciprocal_rank_fusion(scored)
        fused = apply_time_weighting(fused, enabled=config.retrieval.time_weighting)
        fused_props: list[Proposition] = [f.proposition for f in fused]

        if config.retrieval.enable_reranker and fused_props:
            reranked_props = list(reranker.rerank(sub_query, fused_props[:top_k], k=rerank_top_k))
        else:
            reranked_props = fused_props[:rerank_top_k]
        for p in reranked_props:
            all_reranked_props[p.proposition_id] = p

        # Step 4.1's own "several versions" case: two genuinely different
        # numbers for the same period (reported vs restated) -- answer
        # each, labelled, instead of the generator silently blending or
        # preferring one when it only ever sees one merged fact set.
        collision = detect_near_identical_versions(reranked_props)
        if collision is not None:
            near_identical_detected = True
            near_identical_detail = collision.differing_detail
            for sub_answer, version_ffts in answer_each_version(
                sub_query, rendered_question, collision, fff_client, generator_client
            ):
                pair_facts(sub_answer.sub_query, version_ffts, ctx)
                all_ffts.extend(version_ffts)
                sub_answers.append(sub_answer)
            continue

        ffts = rewrite_to_ffts(rendered_question, reranked_props, fff_client, subject_override=sub_query.entity)
        pair_facts(sub_query, ffts, ctx)
        all_ffts.extend(ffts)

        generated = generate_answer(rendered_question, ffts, generator_client)
        sub_answers.append(SubAnswer(sub_query=sub_query, generated=generated))

    draft = assemble_draft(sub_answers)

    verifier = PassthroughVerifier()
    verified = verifier.verify(draft, evidence=all_ffts, ctx=ctx)

    any_answerable = any(sa.generated.answerable and sa.generated.answers for sa in sub_answers)
    if not any_answerable:
        trace: Sequence[VerificationClaimResult] = [
            VerificationClaimResult(claim_id="c_1", claim_text=question, is_numeric=True, verdict="unsupported")
        ]
    else:
        trace = [VerificationClaimResult(claim_id="c_1", claim_text=question, is_numeric=True, verdict="match")]
    decision = decide(trace)

    final_text = reverse_map(verified.answer_text, ctx)

    fff_by_id = {f.fff_id: f for f in all_ffts}
    used_fff_ids = [fid for sa in sub_answers for fid in sa.generated.used_fff_ids]
    used_propositions = [
        all_reranked_props[fff_by_id[fid].proposition_id]
        for fid in used_fff_ids
        if fid in fff_by_id and fff_by_id[fid].proposition_id in all_reranked_props
    ]
    citations = bind_citations(final_text, used_propositions)

    combined_answers: list[AnswerValue] = [av for sa in sub_answers for av in sa.generated.answers]

    return C3AnswerResult(
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
        fff_rewriter_model=fff_rewriter_model,
        generator_model=generator_model,
        query_splitter_model=query_splitter_model,
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
