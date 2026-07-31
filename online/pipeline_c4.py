"""C4 end-to-end online pipeline: hybrid + metadata filter + rerank +
NPCE + CoVe, the full framework. Identical to `online.pipeline_c3` for the
fan-out (split -> remap -> filter -> retrieve -> fuse -> rerank ->
FFF-rewrite -> pair -> generate, once per sub-query) and the draft
assembler -- only the verifier differs, same relationship C2 has to C1.

Abstention trigger for CoVe-on, multi-sub-query: mirrors
`online.pipeline_c2`'s short-circuit exactly, generalized from "no ffts /
not answerable" (one sub-query) to "no ffts at all across every sub-query
/ no sub-query produced anything answerable" (reusing C3's own
`any_answerable` rationale unchanged) -- if nothing was ever retrieved
anywhere, there's nothing for CoVe to verify, so that case is still
synthesized as a single `unsupported` claim rather than calling CoVe on an
empty draft.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from config.schema import ExperimentConfig
from contracts.interfaces import Embedder, Reranker
from contracts.models import PlaceholderRegistryEntry, Proposition, RequestContext, VerificationClaimResult
from llm.client import LLMClient
from online.analysis.query_splitter import LLMQuerySplitter, render_subquery_question
from online.curation.fff_rewriter import rewrite_to_ffts
from online.curation.pairer import pair_facts
from online.generation.assembler import SubAnswer, assemble_draft
from online.output.abstention_gate import decide
from online.output.citation_binder import bind_citations
from online.output.generator import AnswerValue, generate_answer
from online.placeholders.remapper import remap_entity, reverse_map
from online.retrieval.fusion import apply_time_weighting, reciprocal_rank_fusion
from online.retrieval.hybrid import retrieve as hybrid_retrieve
from online.retrieval.metadata_filter import filter_candidates
from online.verification.cove_orchestrator import CoVeVerifier


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
) -> C4AnswerResult:
    """C4's full query -> split -> NPCE -> answer -> CoVe-verified ->
    citation path for one question. Real LLM calls happen inside
    `LLMQuerySplitter.transform`/`rewrite_to_ffts`/`generate_answer`/
    `CoVeVerifier.verify` -- this function is pure orchestration, same as
    C1-C3's."""
    ctx = RequestContext(request_id="c4", config_hash=config.experiment_id)
    top_k = config.retrieval.top_k
    rerank_top_k = config.retrieval.rerank_top_k

    raw_sub_queries = LLMQuerySplitter(llm_client=splitter_client).transform(question, ctx)
    sub_queries = [
        sq.model_copy(update={"entity": remap_entity(sq.entity, ctx, registry=registry)})
        for sq in raw_sub_queries
    ]

    all_ffts = []
    all_reranked_props: dict[str, Proposition] = {}
    sub_answers: list[SubAnswer] = []

    for sub_query in sub_queries:
        rendered_question = render_subquery_question(sub_query)

        candidates = filter_candidates(sub_query, ctx, bundle.metadata_store)
        scored = hybrid_retrieve(rendered_question, candidates, embedder)
        fused = reciprocal_rank_fusion(scored)
        fused = apply_time_weighting(fused, enabled=config.retrieval.time_weighting)
        fused_props: list[Proposition] = [f.proposition for f in fused]

        if config.retrieval.enable_reranker and fused_props:
            reranked_props = list(reranker.rerank(sub_query, fused_props[:top_k], k=rerank_top_k))
        else:
            reranked_props = fused_props[:rerank_top_k]
        for p in reranked_props:
            all_reranked_props[p.proposition_id] = p

        ffts = rewrite_to_ffts(rendered_question, reranked_props, fff_client, subject_override=sub_query.entity)
        pair_facts(sub_query, ffts, ctx)
        all_ffts.extend(ffts)

        generated = generate_answer(rendered_question, ffts, generator_client)
        sub_answers.append(SubAnswer(sub_query=sub_query, generated=generated))

    draft = assemble_draft(sub_answers)
    any_answerable = any(sa.generated.answerable and sa.generated.answers for sa in sub_answers)

    if not all_ffts or not any_answerable:
        trace: Sequence[VerificationClaimResult] = [
            VerificationClaimResult(claim_id="c_1", claim_text=question, is_numeric=True, verdict="unsupported")
        ]
        verified_answer_text = draft
    else:
        verifier = CoVeVerifier(
            cove_claim_extraction_client, cove_independent_verification_client, cove_prose_comparison_client
        )
        verified = verifier.verify(draft, evidence=all_ffts, ctx=ctx)
        verified_answer_text = verified.answer_text
        trace = verified.verification_trace

    decision = decide(trace)
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
    )
