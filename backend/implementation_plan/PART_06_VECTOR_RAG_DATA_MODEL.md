# Master Technical Architecture and Implementation Plan
## Part 6 — Vector / RAG Data Model

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §4.3 (three-index invariant), §12.2 (document
classification and versioning), §9.1–§9.3 (retrieval authorization). Builds on the entities defined
in Part 5 §4.

---

## 1. The Object Chain

```
Document
   │  (1 document, many versions over time)
   ▼
Document Version
   │  (1 version, many chunks — the parsed, page/table-preserving intermediate form)
   ▼
Chunk (Path A) ── or ── Structured Row (Path B, templated directly, no chunk stage)
   │  (LLM-segmented [Path A] or templated [Path B] into atomic facts)
   ▼
Proposition
   │  (embedded exactly once, by the one shared Embedder)
   ▼
Embedding
   │  (written into the dense index; the proposition's structured fields also written
   │   into the sparse index and metadata store, in the same pass)
   ▼
Index (Dense + Sparse + Metadata — three physical stores, one logical object)
```

**Why the chain stops distinguishing "chunk" after Proposition.** A chunk is an intermediate,
pre-segmentation representation that exists only inside the offline pipeline (Part 5 §4,
`document_chunks`). It is retained for re-processing and audit traceability, but it is never
itself a retrievable unit — the Proposition is the atomic retrievable unit, exactly as in the
existing research core's DenseX-granularity design. Path B skips the chunk stage entirely, since a
structured row already is a proposition (architecture §4.4 of the inherited research contracts).

## 2. Metadata Carried at Every Stage

| Stage | Required metadata | Source |
|---|---|---|
| Document | `tenant_id`, `classification`, `content_hash`, `retention_policy_id`, `legal_hold_id` (nullable) | Set at upload (Boundary 12, Part 3) |
| Document Version | `effective_date`, `superseded_by` | Set at ingestion; `superseded_by` set retroactively when a newer version is ingested |
| Chunk / Structured Row | `document_version_id`, `page`, `source_span` | Parser output |
| Proposition | `tenant_id`, `proposition_id`, `document_version_id`, `classification` (inherited from Document), `provenance_tier`, `numeric`, `confidence`, `metadata.page`/`section`/`filing_date`, `created_at` | Segmenter/Row Templating + Metadata Tagger |
| Embedding | `proposition_id`, `tenant_id`, `embedder_model`, `embedder_revision`, `vector_store_namespace` | Index Writers, at write time |

**Every object in the chain carries enough metadata to answer, without a join to any other store:
which tenant, which document and version, what classification, what permissions apply, where it
came from, when it was created, how long it may be retained, and whether it is under legal hold.**
This is deliberate — it means the Retrieval component's authorization pre-filter (§3 below) never
needs to reach outside the metadata store to make its decision.

## 3. Retrieval-Time Authorization — Exact Enforcement Sequence

This is the concrete mechanism behind architecture §9.1 ("at what exact point is authorization
applied") and §9.2 ("can unauthorized documents ever enter the context window — no").

```
1. Retrieval (Part 1 §2.13) receives a SubQuery + SecurityContext.

2. BEFORE any vector similarity search or BM25 scoring runs, Retrieval issues a metadata-store
   query:
       SELECT proposition_id FROM propositions
       WHERE tenant_id = :security_context.tenant_id
         AND classification = ANY(:security_context.data_classification_clearance)
         AND (legal_hold_id IS NULL OR <legal hold does not restrict read access>)
       AND <any ABAC-derived predicate from security_context.abac_attributes, e.g.
            metadata.region = :security_context.abac_attributes.region, where the module's
            policy requires it>

   This produces the candidate ID set — the ONLY proposition_ids the vector and sparse indexes
   are permitted to search within.

3. The dense index query is issued with this candidate set as a hard pre-filter (native
   metadata-filtered vector search where the backend supports it; otherwise the candidate set is
   used to constrain which vectors are compared at all, never as a post-hoc filter on results
   already computed against the full tenant's index).

4. The sparse (BM25) index query is issued against the same pre-filtered candidate set.

5. Fusion (Part 1's RAG Orchestration) combines only the pre-filtered results from both arms.

6. Reranking (Part 1 §2.14) operates strictly on this already-authorized set — it has no
   visibility into any proposition outside it, so it cannot "prefer" or surface an unauthorized
   document even if it happened to be topically relevant.

7. The Proposition/Evidence Layer's FFF rewrite step (§2.15) operates on the reranked,
   already-authorized propositions only.
```

**Result: an unauthorized proposition is never fetched from the vector or sparse store, never
scored, never reranked, and never reaches the Context Builder or the LLM.** This answers
architecture §9.2 and §9.3 directly — unauthorized content cannot influence embeddings or
reranking because it is excluded before either operation runs, not filtered out afterward.

## 4. Cached Retrieval Results and Permission Changes

Architecture §9.4/§9.5 ask whether cached retrieval results can cross permission boundaries, and
whether a permission change invalidates a cache entry.

- **Cache key (Part 1 §2.38) includes the resolved `data_classification_clearance` and
  `abac_attributes` fingerprint**, not just `tenant_id` and `user_id` — so a change in a user's
  clearance produces a different cache key on their next request, meaning a stale cache entry
  computed under their *old* permissions is simply never looked up again; it ages out via the
  cache's normal TTL rather than requiring an active invalidation sweep.
- For highly sensitive queries, no caching occurs at all (architecture §11.5), which removes this
  concern entirely for that class of query.

## 5. Document Versions and Historical Queries

Architecture §9.6/§9.7 — how document versions are handled, and how a historical-policy query
avoids mixing versions.

- Every `document_versions` row carries an `effective_date` and, once superseded, a
  `superseded_by` pointer (Part 5 §4).
- A query's Query Router (Part 1 §2.5, §2.12) determines, from slot parsing, whether the question
  is time-anchored ("what was the policy in 2023") or asks for the current state by default (the
  overwhelming majority of questions).
- The Metadata Filter step of Retrieval resolves which `document_version_id` set is in scope:
  **current-state queries filter to the version whose `superseded_by IS NULL`** (the latest);
  **time-anchored queries filter to the version whose `effective_date` range covers the requested
  date.**
- **These two filters are mutually exclusive at the SQL level** — a single retrieval call never
  returns propositions from more than one version of the same document for the same logical query,
  which is what prevents the "blending" failure mode named in architecture §12.2.

## 6. The Three-Index Invariant, Verified

The invariant (architecture §2.1, §4.3) — dense index, sparse index, and metadata store contain
**identical `(tenant_id, proposition_id)` sets** — is verified, not merely assumed, by:

- **Write-time guarantee.** Index Writers (Part 1, inherited from the research core's
  `offline/indexing/index_writers.py`) write to all three stores in one logical operation; a
  partial failure (e.g., dense write succeeds, sparse write fails) triggers a rollback of the
  successful writes, never a silent partial index.
- **Post-build assertion.** After any indexing run (initial build, incremental update, or a
  retroactive enrichment like the research core's period-tagging backfill), an automated check
  confirms `count(dense) == count(sparse) == count(metadata)` for the affected `tenant_id` before
  the build is marked complete — this is a release gate for the ingestion pipeline, not an
  optional health check (feeds directly into Part 23's test matrix).

---

*End of Part 6. Part 7 (Conversation & Memory Architecture) follows immediately.*
