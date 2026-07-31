"""C1 end-to-end online pipeline: hybrid + metadata filter + rerank, NPCE
off, CoVe off. Question in, cited answer out, over the frozen offline
index.

Wires, in order: retrieval (dense+sparse+RRF, reused from
`evaluation.run_retrieval_gate` — see that module's docstring for why it
doesn't call `online.retrieval.hybrid.retrieve` directly at corpus scale)
-> cross-encoder rerank -> FFF rewriter -> generator -> `PassthroughVerifier`
(CoVe off) -> citation binder -> abstention gate.

No draft assembler: NPCE off means `PassthroughQueryTransformer` always
emits exactly one sub-query — there's nothing to assemble; the generator's
own `answer_text` already is the draft `D`. Building a templating module
whose input is always a single item would be scope this pipeline doesn't
need yet; C3/C4 (multiple sub-queries) need a real one.

Abstention trigger for CoVe-off: with CoVe off, `PassthroughVerifier` never
populates a verification trace — there's nothing for the abstention gate's
existing rule (`online/output/abstention_gate.py:decide`, unchanged from
its CoVe-on interface) to inspect unless this pipeline constructs one. So:
no FFFs at all, or the generator returning `answerable: false`, is
translated into a single synthetic `VerificationClaimResult` with
`verdict="unsupported"` before calling `decide` — the same function CoVe
(C2) will later feed with its own real, multi-claim trace. The abstention
gate itself doesn't need to know whether CoVe ran.
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
from online.verification.cove_orchestrator import PassthroughVerifier


@dataclass(frozen=True)
class C1AnswerResult:
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


def answer_question(
    question: str,
    bundle: Any,  # offline.indexing.build.IndexBundle -- duck-typed, see evaluation.run_retrieval_gate
    embedder: Embedder,
    reranker: Reranker,
    fff_client: LLMClient,
    generator_client: LLMClient,
    config: ExperimentConfig,
    fff_rewriter_model: str,
    generator_model: str,
) -> C1AnswerResult:
    """C1's full query -> answer -> citation path for one question. Real
    LLM calls happen inside `rewrite_to_ffts`/`generate_answer` — this
    function is pure orchestration."""
    ctx = RequestContext(request_id="c1", config_hash=config.experiment_id)
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

    verifier = PassthroughVerifier()
    verified = verifier.verify(generated.answer_text, evidence=ffts, ctx=ctx)

    if not ffts or not generated.answerable:
        trace: Sequence[VerificationClaimResult] = [
            VerificationClaimResult(claim_id="c_1", claim_text=question, is_numeric=True, verdict="unsupported")
        ]
    else:
        trace = [VerificationClaimResult(claim_id="c_1", claim_text=question, is_numeric=True, verdict="match")]
    decision = decide(trace)

    fff_by_id = {f.fff_id: f for f in ffts}
    prop_by_id = {p.proposition_id: p for p in reranked_props}
    used_propositions = [
        prop_by_id[fff_by_id[fid].proposition_id]
        for fid in generated.used_fff_ids
        if fid in fff_by_id and fff_by_id[fid].proposition_id in prop_by_id
    ]
    citations = bind_citations(verified.answer_text, used_propositions)

    return C1AnswerResult(
        question=question,
        abstained=decision.abstained,
        abstention_message=decision.message,
        answerable=generated.answerable,
        answers=generated.answers,
        answer_text=verified.answer_text,
        citations=citations,
        used_fff_ids=generated.used_fff_ids,
        retrieved_page_count=len(reranked_props),
        fff_count=len(ffts),
        fff_rewriter_model=fff_rewriter_model,
        generator_model=generator_model,
    )
