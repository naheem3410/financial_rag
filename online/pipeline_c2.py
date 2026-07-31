"""C2 end-to-end online pipeline: hybrid + metadata filter + rerank, NPCE
off, CoVe on. Identical retrieval core and FFF/generator stages to C1
(`online.pipeline_c1`) — only the verifier differs, since toggling a cell
just swaps the implementation bound to `QueryTransformer` and `Verifier`.
This module is mostly a thin variant of `pipeline_c1.answer_question`; see
that module's docstring for the shared retrieval/FFF/generator rationale
not repeated here.

Abstention trigger for CoVe-on: unlike C1 (which must synthesize a
verification trace because `PassthroughVerifier` never populates one), C2
feeds the abstention gate (`online/output/abstention_gate.py:decide`) the
real, multi-claim trace `CoVeVerifier.verify()` produces. The "no FFFs /
not answerable" short-circuit is preserved unchanged from C1: there's
nothing for CoVe to verify if the generator never produced a groundable
draft, so that case is still synthesized as a single `unsupported` claim
rather than calling CoVe on an empty or abstained draft.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from config.schema import ExperimentConfig
from contracts.interfaces import Embedder, Reranker
from contracts.models import Proposition, RequestContext, VerificationClaimResult
from evaluation.run_retrieval_gate import retrieve_fused
from llm.client import LLMClient
from online.analysis.query_splitter import PassthroughQueryTransformer
from online.curation.fff_rewriter import rewrite_to_ffts
from online.output.abstention_gate import decide
from online.output.citation_binder import bind_citations
from online.output.generator import AnswerValue, generate_answer
from online.verification.cove_orchestrator import CoVeVerifier


@dataclass(frozen=True)
class C2AnswerResult:
    question: str
    abstained: bool
    abstention_message: str
    answerable: bool
    answers: list[AnswerValue]
    answer_text: str
    citations: list[dict[str, Any]]
    used_fff_ids: list[str]
    retrieved_page_count: int
    fff_count: int
    fff_rewriter_model: str
    generator_model: str
    cove_verification_model: str
    verification_trace: list[dict[str, Any]]


def answer_question(
    question: str,
    bundle: Any,  # offline.indexing.build.IndexBundle -- duck-typed, see evaluation.run_retrieval_gate
    embedder: Embedder,
    reranker: Reranker,
    fff_client: LLMClient,
    generator_client: LLMClient,
    cove_claim_extraction_client: LLMClient,
    cove_independent_verification_client: LLMClient,
    cove_prose_comparison_client: LLMClient,
    config: ExperimentConfig,
    fff_rewriter_model: str,
    generator_model: str,
    cove_verification_model: str,
) -> C2AnswerResult:
    """C2's full query -> answer -> CoVe-verified -> citation path for one
    question. Real LLM calls happen inside `rewrite_to_ffts`/`generate_answer`/
    `CoVeVerifier.verify` — this function is pure orchestration, same as C1."""
    ctx = RequestContext(request_id="c2", config_hash=config.experiment_id)
    top_k = config.retrieval.top_k
    rerank_top_k = config.retrieval.rerank_top_k

    fused = retrieve_fused(question, bundle, embedder, top_k=top_k, time_weighting=config.retrieval.time_weighting)
    fused_props: list[Proposition] = [f.proposition for f in fused]

    sub_query = PassthroughQueryTransformer().transform(question, ctx)[0]
    if config.retrieval.enable_reranker and fused_props:
        reranked_props = list(reranker.rerank(sub_query, fused_props[:top_k], k=rerank_top_k))
    else:
        reranked_props = fused_props[:rerank_top_k]

    ffts = rewrite_to_ffts(question, reranked_props, fff_client)
    generated = generate_answer(question, ffts, generator_client)

    if not ffts or not generated.answerable:
        trace: Sequence[VerificationClaimResult] = [
            VerificationClaimResult(claim_id="c_1", claim_text=question, is_numeric=True, verdict="unsupported")
        ]
        verified_answer_text = generated.answer_text
    else:
        verifier = CoVeVerifier(
            cove_claim_extraction_client, cove_independent_verification_client, cove_prose_comparison_client
        )
        verified = verifier.verify(generated.answer_text, evidence=ffts, ctx=ctx)
        verified_answer_text = verified.answer_text
        trace = verified.verification_trace

    decision = decide(trace)

    fff_by_id = {f.fff_id: f for f in ffts}
    prop_by_id = {p.proposition_id: p for p in reranked_props}
    used_propositions = [
        prop_by_id[fff_by_id[fid].proposition_id]
        for fid in generated.used_fff_ids
        if fid in fff_by_id and fff_by_id[fid].proposition_id in prop_by_id
    ]
    citations = bind_citations(verified_answer_text, used_propositions)

    return C2AnswerResult(
        question=question,
        abstained=decision.abstained,
        abstention_message=decision.message,
        answerable=generated.answerable,
        answers=generated.answers,
        answer_text=verified_answer_text,
        citations=citations,
        used_fff_ids=generated.used_fff_ids,
        retrieved_page_count=len(reranked_props),
        fff_count=len(ffts),
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
