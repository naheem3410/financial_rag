# Master Technical Architecture and Implementation Plan
## Part 8 — RAG Request Pipeline

The complete Fixed Faithfulness Pipeline (`ENTERPRISE_PLATFORM_ARCHITECTURE.md` §2.1, Controlled
mode), step by step, with the responsible component from Part 1 named at each stage. This is the
request-time sequence diagram; Part 1 defines each component's own responsibilities in isolation,
this part shows how they compose into one answer.

---

## Pipeline Diagram

```
User request
     │
     ▼
[1] Authentication ─────────────────────── Authentication Service (Part 1 §2.5)
     │  produces: validated SecurityContext (Part 4)
     ▼
[2] Authorization (coarse) ─────────────── API Gateway + Authorization Engine (§2.2, §2.6)
     │  checks: is this module reachable by this role at all
     ▼
[3] Intent / module resolution ─────────── API Gateway routes to the module's configured
     │                                      orchestration mode (Controlled — this pipeline)
     │                                      per architecture §2.3; mode is read from the
     │                                      module's static config, never inferred
     ▼
[4] Query transformation ───────────────── Query Processing (§2.12): collision detection,
     │                                      slot parsing (LLM Call Site 3), deterministic
     │                                      Cartesian-product decomposition, Query Router's
     │                                      direct/rewrite/decompose/full-NPCE selection
     │  produces: one or more SubQuery objects
     ▼
[5] Retrieval ───────────────────────────── Retrieval (§2.13)
     │  produces: a permission-pre-filtered candidate proposition set (Part 6 §3 — this IS
     │  the authorization-filtering step; it is not a separate stage after retrieval, it is
     │  structurally inseparable from it)
     ▼
[6] Reranking ───────────────────────────── Reranking (§2.14)
     │  produces: top-k reranked propositions, still within the authorized set
     ▼
[7] Evidence / FFF construction ─────────── Proposition/Evidence Layer (§2.15): FFF Rewriter
     │                                      (LLM Call Site 4, numeric/period-verbatim guarded),
     │                                      Fact-set/Query Pairer
     │  produces: paired FFF records per SubQuery
     ▼
[8] Context construction ───────────────── Context Builder (§2.10): assembles the LLM-facing
     │                                      payload from the paired FFFs plus any authorized
     │                                      same-session conversation history — this is the
     │                                      concrete enforcement point for Principle 4
     ▼
[9] Generation ──────────────────────────── LLM Gateway (§2.16): Generator (LLM Call Site 5),
     │                                      one call per SubQuery, answering from its paired
     │                                      FFF only
     │  produces: sub-answers {a_1..a_n}
     ▼
[9a] Draft assembly ─────────────────────── RAG Orchestration: Draft Assembler (deterministic
     │                                      templating, introduces no facts) combines
     │                                      {a_1..a_n} into draft D
     ▼
[10] Verification ───────────────────────── CoVe Orchestrator (inherited): claim extraction
     │                                      (LLM), independent execution against paired FFF
     │                                      evidence with D structurally excluded (LLM Call
     │                                      Site 6), numeric-claim revision via Guardrails'
     │                                      deterministic comparator (§2.19), prose-claim
     │                                      revision via LLM
     │  produces: revised answer V + per-claim verification trace
     ▼
[11] Confidence ─────────────────────────── Confidence Engine (§2.20): composite score from
     │                                      retrieval/rerank scores, guard results, CoVe match
     │                                      rate, extraction tier, OCR confidence
     ▼
[12] Guardrails (final check) ───────────── Guardrails (§2.19) + Abstention Gate: if a material
     │                                      claim failed verification, or confidence is below
     │                                      the applicable floor, the answer is downgraded to
     │                                      an abstention for that claim, or the whole answer,
     │                                      per the Abstention Gate's rules — never silently
     │                                      passed through
     │
     ├── below threshold / material failure ──▶ [12a] Human Escalation Service (§2.26) —
     │                                            architecture §9 fail-closed rules apply
     │
     ▼ (passes)
[13] Response assembly ─────────────────── RAG Orchestration
     ▼
[14] Citation ───────────────────────────── Citation Binder (deterministic, inherited rule: an
     │                                      LLM never writes a citation) — looks up
     │                                      (filing_type, filing_date, page, source_uri) from
     │                                      each surviving proposition_id
     ▼
[15] Placeholder reverse-mapping ────────── Placeholder Reverse-Mapper (deterministic) restores
     │                                      real entity names/identifiers from the per-request
     │                                      placeholder map (never a global, architecture §5.4
     │                                      of the inherited research contracts)
     ▼
[16] Audit ──────────────────────────────── Audit Service (§2.27): the full trace — query,
     │                                      security context, retrieved evidence IDs, model/
     │                                      prompt versions used, confidence, guard results,
     │                                      verification trace, final decision — written as one
     │                                      logical audit record before the response is
     │                                      returned to the client
     ▼
Response + citations + confidence returned to caller
```

## Per-Step Responsibility Table

| Step | Responsible component (Part 1 ref) | LLM involved? |
|---|---|---|
| Authentication | Authentication Service (§2.5) | No |
| Authorization (coarse) | API Gateway + Authorization Engine (§2.2, §2.6) | No |
| Intent/module resolution | API Gateway | No |
| Query transformation | Query Processing (§2.12) | Yes — Call Site 3 (slot parsing only; decomposition itself is deterministic) |
| Retrieval (incl. authorization filtering) | Retrieval (§2.13) | No |
| Reranking | Reranking (§2.14) | No (cross-encoder, non-generative) |
| Evidence/FFF construction | Proposition/Evidence Layer (§2.15) | Yes — Call Site 4, guarded |
| Context construction | Context Builder (§2.10) | No |
| Generation | LLM Gateway (§2.16) | Yes — Call Site 5 |
| Draft assembly | RAG Orchestration | No |
| Verification | CoVe Orchestrator (inherited) + Guardrails (§2.19) | Yes — Call Site 6 (numeric comparison itself is not) |
| Confidence | Confidence Engine (§2.20) | No |
| Guardrails/abstention | Guardrails (§2.19) | No |
| Human escalation (conditional) | Human Escalation Service (§2.26) | No |
| Response assembly | RAG Orchestration | No |
| Citation | Citation Binder (inherited) | No |
| Placeholder reverse-mapping | Placeholder Reverse-Mapper (inherited) | No |
| Audit | Audit Service (§2.27) | No |

**Every LLM-involved step is one of the registered call sites (Part 1 §2.6). No step in this
pipeline invokes an LLM outside that registry — this table is itself the verification that the
pipeline as built matches the call-site discipline stated as Principle 1.**

## Failure Behavior at Each Stage

Consistent with Principle 5 (fail-closed) throughout: authentication failure → 401, no session
created. Authorization failure → 403, logged. Retrieval unavailable → abstention. Reranker
unavailable → degrade to fused order, do not fail the request. FFF guard failure → drop the fact,
never repair. Generation failure (schema-invalid) → bounded retry then drop. CoVe failure on a
material claim → abstain on that claim or the whole answer per the Abstention Gate. Confidence
below floor → escalate, never silently answer. Audit write failure → block the response for
security-critical steps rather than return unaudited (Part 1 §2.27).

---

*End of Part 8. Part 9 (Agentic Pipeline) follows immediately.*
