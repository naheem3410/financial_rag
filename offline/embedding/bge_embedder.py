"""Shared embedder — BAAI/bge-base-en-v1.5 via sentence-transformers.

One embedding component, used by both the placeholder registry builder
(similarity clustering for collision candidates) and the index writers
(dense index). Not two instantiations: the dense index needs to be built by
exactly one embedding path, or a registry-vs-index embedding drift would
silently produce two different notions of "similar" inside the same
system. Both call sites take a shared `Embedder` (contracts/interfaces.py)
instance rather than constructing their own — see
`offline/registry/placeholder_builder.py` and
`offline/indexing/index_writers.py`.

Why this model, pinned this way: results need to be reproducible from the
run manifest — an API embedder can be silently revised or deprecated out
from under a run. Shipping confidential filings to a third-party embedding
API also cuts against on-prem enterprise deployment for an audited finance
setting, before the system even reaches the LLM. BAAI/bge-base-en-v1.5 also
pairs with the already-specified `bge-reranker` — same lab, same training
recipe family.

Pinning: HuggingFace checkpoints can be updated in place at a fixed model
name, so the revision (commit) hash is pinned, not just the model name.
Re-pin deliberately — never silently — if the checkpoint needs updating,
and record the new hash here and in every subsequent run manifest.
`model_name`, `revision`, and `dimension` should be written to the run
manifest on every run.

Asymmetric query prefix: BGE models need "Represent this sentence for
searching relevant passages: " prepended to queries; passages get no
prefix. Getting this backwards degrades retrieval measurably and silently.
`encode_queries` and `encode_passages` are two distinct methods
(contracts.interfaces.Embedder) rather than one `encode` with an `is_query`
flag, so no call site can forget which one it needed.
"""
from __future__ import annotations

from typing import Sequence

MODEL_NAME = "BAAI/bge-base-en-v1.5"
# Pinned commit hash, resolved via the HuggingFace Hub API -- see module docstring.
MODEL_REVISION = "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"
EXPECTED_DIMENSION = 768

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def apply_query_prefix(texts: Sequence[str]) -> list[str]:
    """The asymmetric-prefix rule itself, isolated as a pure function so it
    is unit-testable without loading the model (tests/test_bge_embedder.py)."""
    return [f"{QUERY_PREFIX}{t}" for t in texts]


class BGEEmbedder:
    """Concrete `Embedder` (contracts/interfaces.py). Loads the model lazily
    on first use, not at import or construction time — importing this module
    or constructing a `BGEEmbedder` never requires network access or model
    init; only the first call to `encode_queries`/`encode_passages`/
    `dimension` does.
    """

    def __init__(self, model_name: str = MODEL_NAME, revision: str = MODEL_REVISION) -> None:
        self._model_name = model_name
        self._revision = revision
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name, revision=self._revision)
        return self._model

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def dimension(self) -> int:
        return self._load().get_embedding_dimension()

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self._load().encode(list(texts), convert_to_numpy=True).tolist()

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._load().encode(apply_query_prefix(texts), convert_to_numpy=True).tolist()
