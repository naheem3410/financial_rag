"""C0 end-to-end online pipeline: naive RAG, fixed-size chunks, dense-only,
no rerank. Outside the 2x2 factorial — a reference point, not the
baseline; never conflate with C1. Question in, answer out, over a separate
fixed-size-chunk index (`offline/chunking/fixed_size_chunker.py`), not the
proposition index C1-C4 share.

Wires, in order: dense-only vector search (no sparse arm, no RRF fusion,
no metadata filter, no cross-encoder rerank — the top-`rerank_top_k`
chunks by raw dense score are simply taken as-is) -> naive generator
(`online.output.naive_generator`, reuses the generator's config slot with
an unguarded prompt) -> `PassthroughVerifier` (CoVe is not part of this
cell) -> citation binder -> abstention gate, mirroring C1's own CoVe-off
abstention synthesis (`online/pipeline_c1.py`'s docstring) since there's
equally nothing for a verification trace to record here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from config.schema import ExperimentConfig
from contracts.interfaces import Embedder
from contracts.models import RequestContext, VerificationClaimResult
from llm.client import LLMClient
from online.output.abstention_gate import decide
from online.output.citation_binder import bind_citations
from online.output.naive_generator import AnswerValue, generate_naive_answer
from online.verification.cove_orchestrator import PassthroughVerifier


@dataclass(frozen=True)
class C0AnswerResult:
    question: str
    abstained: bool
    abstention_message: str
    answerable: bool
    answers: list[AnswerValue]
    answer_text: str
    citations: list[dict[str, Any]]
    used_chunk_ids: list[str]
    retrieved_chunk_count: int
    generator_model: str


def answer_question(
    question: str,
    bundle: Any,  # offline.indexing.build.IndexBundle -- duck-typed, dense_index + metadata_store only
    embedder: Embedder,
    generator_client: LLMClient,
    config: ExperimentConfig,
    generator_model: str,
) -> C0AnswerResult:
    """C0's full query -> answer -> citation path for one question. The
    real LLM call happens inside `generate_naive_answer` -- this function
    is pure orchestration, same shape as C1-C4's."""
    ctx = RequestContext(request_id="c0", config_hash=config.experiment_id)
    top_k = config.retrieval.top_k
    rerank_top_k = config.retrieval.rerank_top_k

    [query_vector] = embedder.encode_queries([question])
    dense_hits = bundle.dense_index.search(query_vector, k=top_k)
    # No rerank (config.retrieval.enable_reranker is false for C0): the
    # top-rerank_top_k chunks by raw dense-similarity score are taken as-is
    # -- this *is* "no rerank," not a stand-in for it.
    top_ids = [pid for pid, _score in dense_hits[:rerank_top_k]]
    chunks = [bundle.metadata_store.get(pid) for pid in top_ids]

    generated = generate_naive_answer(question, chunks, generator_client)

    verifier = PassthroughVerifier()
    verified = verifier.verify(generated.answer_text, evidence=[], ctx=ctx)

    if not chunks or not generated.answerable:
        trace: Sequence[VerificationClaimResult] = [
            VerificationClaimResult(claim_id="c_1", claim_text=question, is_numeric=True, verdict="unsupported")
        ]
    else:
        trace = [VerificationClaimResult(claim_id="c_1", claim_text=question, is_numeric=True, verdict="match")]
    decision = decide(trace)

    chunk_by_id = {c.proposition_id: c for c in chunks}
    used_chunks = [chunk_by_id[cid] for cid in generated.used_chunk_ids if cid in chunk_by_id]
    citations = bind_citations(verified.answer_text, used_chunks)

    return C0AnswerResult(
        question=question,
        abstained=decision.abstained,
        abstention_message=decision.message,
        answerable=generated.answerable,
        answers=generated.answers,
        answer_text=verified.answer_text,
        citations=citations,
        used_chunk_ids=generated.used_chunk_ids,
        retrieved_chunk_count=len(chunks),
        generator_model=generator_model,
    )
