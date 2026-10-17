"""Core interfaces for the pipeline.
Swapping cells just rebinds QueryTransformer/Verifier; nothing else changes."""
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
    """Shared embedding component; one binding used everywhere so registry and index never drift apart.
    Separate query/passage methods because BGE needs an asymmetric query prefix a bool flag could forget to set."""

    @property
    def model_name(self) -> str: ...

    @property
    def revision(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def encode_passages(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def encode_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
