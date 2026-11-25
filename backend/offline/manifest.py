"""Run manifest — config hash, pinned model version, seed, index build id,
corpus checksum, per-component version. Results without one aren't
reportable. Multi-provider routing means one row per call site actually
used, so a run must be fully reconstructable from this alone.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Optional

from llm.routing import LLMCallSiteManifest
from offline.indexing.build import BuildManifest


def compute_config_hash(config_dict: dict) -> str:
    """Deterministic hash of the resolved config. Any difference, even a
    silently-changed default, changes the hash."""
    canonical = json.dumps(config_dict, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RunManifest:
    config_hash: str
    index_build: BuildManifest
    llm_call_sites: tuple[LLMCallSiteManifest, ...]
    # Run-execution stats, distinct from index_build's build-time facts --
    # describes how the segmentation pass that produced those propositions ran.
    total_requests: Optional[int] = None
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    total_cost_usd: Optional[float] = None
    effective_concurrency: Optional[float] = None

    @property
    def indexed_doc_names(self) -> tuple[str, ...]:
        return self.index_build.indexed_doc_names

    def to_dict(self) -> dict:
        return {
            "config_hash": self.config_hash,
            "index_build": asdict(self.index_build),
            "llm_call_sites": [asdict(cs) for cs in self.llm_call_sites],
            "total_requests": self.total_requests,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": self.total_cost_usd,
            "effective_concurrency": self.effective_concurrency,
        }
