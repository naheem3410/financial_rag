"""Core interfaces for the pipeline.

Switching an experiment cell just swaps the implementation bound to
`QueryTransformer` and `Verifier`. Nothing else changes between cells.
"""
from __future__ import annotations

from typing import Protocol, Sequence

from contracts.models import FFF, Proposition, RequestContext, SubQuery, VerifiedAnswer


class Retriever(Protocol):
    def retrieve(self, sq: SubQuery, k: int) -> Sequence[Proposition]: ...


class Reranker(Protocol):
    def rerank(
        self, sq: SubQuery, props: Sequence[Proposition], k: int
    ) -> Sequence[Proposition]: ...


class ContextCurator(Protocol):
    """FFF rewriter. The NPCE-off implementation is a pass-through."""

    def curate(self, sq: SubQuery, props: Sequence[Proposition]) -> Sequence[FFF]: ...


class QueryTransformer(Protocol):
    """Query splitter. The NPCE-off implementation returns a single,
    identity sub-query — no split, no collision detection."""

    def transform(self, query: str, ctx: RequestContext) -> Sequence[SubQuery]: ...


class Verifier(Protocol):
    """Chain-of-Verification. The CoVe-off implementation passes the draft
    straight through to citation binding, unmodified."""

    def verify(
        self, draft: str, evidence: Sequence[FFF], ctx: RequestContext
    ) -> VerifiedAnswer: ...


class Embedder(Protocol):
    """Shared embedding component, used by both the placeholder registry
    builder and the index writers. Exactly one binding of this protocol is
    used across both — the dense index has to be built by a single
    embedding path, or a registry-vs-index embedding drift would silently
    produce two different notions of "similar" in the same system.

    `encode_queries` and `encode_passages` are deliberately two separate
    methods rather than one `encode(text, is_query: bool)` — BGE-family
    models need an asymmetric query prefix
    ("Represent this sentence for searching relevant passages: ") that
    passages must not get, and a boolean flag is a detail a call site can
    forget to set correctly. Two methods means there is nothing to forget.

    `model_name`, `revision`, and `dimension` should be recorded in the run
    manifest on every run — an unpinned embedder breaks reproducibility, and
    a dimension mismatch against a vector store should fail loudly rather
    than write a corrupt index (see offline/indexing/index_writers.py).
    """

    @property
    def model_name(self) -> str: ...

    @property
    def revision(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def encode_passages(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def encode_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
