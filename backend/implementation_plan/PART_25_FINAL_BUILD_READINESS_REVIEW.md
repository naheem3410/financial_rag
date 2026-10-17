# Master Technical Architecture and Implementation Plan
## Part 25 — Final Build Readiness Review

The closing assessment of Parts 1–24 against `ENTERPRISE_PLATFORM_ARCHITECTURE.md`. Written to be
honest rather than reassuring, per the standard set by that document's own §23 review.

---

## Architecture Frozen

The following are fixed and must not be re-litigated during implementation without a formal
change (Part 16's DRAFT → ... → PRODUCTION lifecycle applied to the architecture itself, not just
its configuration):

- The eight governing principles (constrained LLM surface, authoritative-systems-remain-
  authoritative, three-way separation, no-data-without-authorization, fail-closed, config-over-
  code, defense-in-depth, test-everything).
- The two-lane orchestration model (Fixed Faithfulness Pipeline / Agentic Research Mode) and the
  module-gating rule that a module's mode is static configuration, never runtime-inferred.
- The SecurityContext object's shape and immutability rule (Part 4 §1).
- The eleven-dimension Connector Framework and the auth/binding separation within it (Part 10).
- The transaction state machine and idempotency-key discipline (Part 11).
- The hash-chained, append-only audit architecture (Part 14) — the architecture decision, not the
  storage backend.
- The three-index invariant, extended with `tenant_id` (Part 6).
- The PII tokenization/resolution boundary (Part 13 §4).
- The module-gated orchestration mode assignment table (architecture §2.3).
- The eight-phase implementation order and its stated dependency rule (Part 22).

## Implementation Choices

Engineers may choose freely, without changing the architecture, among:

- The relational database engine (Postgres assumed by convention, not mandated).
- The vector store backend (FAISS-equivalent, Qdrant, pgvector, or a managed service).
- The audit log's physical storage backend (relational table with enforced append-only semantics,
  WORM-capable object store, or a specialized immutable-ledger service) — the hash-chain schema
  and verification logic in Part 14 are backend-agnostic by design.
- The PII detection adapter (Presidio, a vendor-provided model, or deterministic regex-only for a
  minimal first pass) — behind the `PIIDetector` interface (Part 13 §1).
- The secrets backend (HashiCorp Vault, a cloud KMS/Secrets Manager, or equivalent).
- The message queue technology (RabbitMQ, SQS, or equivalent).
- The reranker checkpoint and embedding model (inherited `[implementation choice]` designation
  from the research core, unchanged here).
- The specific observability stack beneath Langfuse for infrastructure metrics.
- The exact hashing/verification mechanism for confirming a bank-hosted local model's artifact
  matches its approved version at load time (Part 16 §3's local-model pinning addition) — the
  requirement is fixed, the specific tool is not.

## Business Decisions Required

These cannot be resolved by engineering judgment alone — each needs stakeholder or bank sign-off:

1. **Regulatory sign-off per jurisdiction** (architecture §20.2) — this document's compliance
   mapping is a starting point, not a substitute for actual legal/compliance review before any
   real bank's go-live.
2. **Exact numeric defaults for confidence thresholds, agent execution budgets, and rate limits**
   — every specific number in this plan is stated as an illustrative default; production values
   need validation against real (or realistically synthesized) usage, and ultimately reflect each
   bank's own risk appetite.
3. **Audit and log retention minimums per jurisdiction** — banking regulation often sets a floor
   the platform's tenant-configurable retention cannot go below; the exact floor is a legal
   research question, not an architecture one.
4. **Penetration-testing and security-review cadence** (Part 17, Part 22 Phase 7) — how often,
   and by whom, is a budget and risk-tolerance decision.
5. **The Model Registry's approach to detecting a provider's silent model-weight change where no
   stable version identifier is exposed** (Part 16 §3) — the canary-comparison approach proposed
   there is a reasonable engineering mitigation, not a guaranteed detection mechanism; how much
   investment this deserves is a risk-tolerance decision.
6. **Which pricing/deployment-uplift percentages actually populate the Plan Registry** —
   architecture §22.5 leaves these as configurable placeholders by design; someone must set them.
7. **Which banks, in which order, form the Phase 5/7 pilot sequence** — a go-to-market decision,
   not an architecture one.
8. **Resolved as a default, not left open:** the Audit Service store is provisioned on
   infrastructure physically separate from the general Relational Database from Phase 0 (Part
   19's updated note) — migrating a live, hash-chained log after it holds real history risks
   re-serialization hash mismatches, write-ordering forks that mimic tampering, and compliance
   optics no bank should have to accept. The remaining decision, genuinely open per tenant, is
   narrower: **whether a specific on-premises bank chooses to explicitly opt out of this default**
   and co-locate its own audit store with its own general database, accepting that deployment's
   database RTO for its own audit trail as a documented trade-off it owns.

## MVP Scope

Per Part 21 and Part 22: Document Intelligence (inherited), Enterprise Knowledge Assistant, AI
Banking Assistant, Compliance Assistant, and Regulatory Intelligence (the first Agentic-mode
module) — built across Phases 0–7 in the order specified, with every phase's stated acceptance
criteria genuinely met before the next phase begins.

## Deferred Scope

Explicitly not part of the MVP, restated from the architecture document's §26 and this plan's own
phase boundaries:

- Every module in Part 21's "Every Other Module — Deferred" list.
- Full noisy-neighbor resource isolation tuning (present as a basic mechanism from Phase 1, tuned
  post-launch).
- Agentic trajectory replay tooling beyond the raw `agent_steps` record (a UI for replay, not the
  data itself, which exists from Phase 6).
- Configuration-rollback UI polish (the underlying versioning mechanism ships in Phase 3; a
  dedicated UI can lag).
- Web-search retrieval and any Program-of-Thoughts extension beyond the Phase 2 calculation
  registry's starter set.
- Regulatory mappings for jurisdictions beyond the initial target market.
- Write-capable actions in AI Banking Assistant (self-service transactions) beyond what Phase 5
  ships read-only.

## Remaining Risks (Honestly Stated, Not Smoothed Over)

- **The `tenant_id` amendment to the previously-frozen Proposition schema (Part 5 §4) carries real
  regression risk** to CoVe's numeric comparison behavior — Phase 2's acceptance criterion
  (reproducing prior FinanceBench-Collision results) is the mitigation, but this is a genuine risk
  until that specific test passes, not a theoretical one.
- **A provider's silent model change is not fully technically detectable** (Business Decision 5
  above) — this is a structural limitation of relying on third-party LLM providers, not a gap in
  this plan's design.
- **On-prem license enforcement's contractual backstop assumes the bank's good faith to a real
  degree** (architecture §22.9's own honest admission) — no technical measure fully closes this for
  a bank with root access to its own hardware; the mitigation is contractual and reputational, and
  its actual deterrent strength is a business judgment, not something this plan can prove.
- **The exhaustive module-by-module review (Part 21's template) has been fully completed for only
  five of nineteen modules** — every other module carries unknown-unknowns until it is actually
  scoped through that template.
- **Synthesized test fixtures (Principle 8) are not real bank data** — Phase 2 and Phase 4's
  acceptance criteria are proven against realistic synthetic scenarios, and the first real bank
  pilot (Phase 7) is the actual proof that those fixtures were realistic enough; this gap is
  structural to building ahead of a signed banking customer, not a flaw in execution.
- **Agentic-mode cost and latency variance is inherently harder to bound tightly than the Fixed
  pipeline's** — Part 9's execution budget caps the worst case, but typical-case cost/latency
  under real usage is genuinely unknown until Phase 6 produces real data.

## Build Readiness Score

| Area | Status |
|---|---|
| Architecture completeness | READY |
| Security completeness (design) | READY |
| Security completeness (proven in running code) | NEEDS TESTING — every adversarial test in Part 23 exists as a specification, none has yet been executed against a running system |
| Data architecture | READY |
| Integration architecture | READY — with NEEDS TESTING against a real (not synthesized) bank system once one is available (Phase 4/7 boundary) |
| AI/RAG architecture | READY — largely inherited and proven by the existing research core's own evaluation results; NEEDS TESTING specifically on the multi-tenant schema amendment (Phase 2's acceptance criterion) |
| Agent architecture | READY (design) / NEEDS IMPLEMENTATION — Part 9's execution-budget mechanism does not yet exist in code |
| Operational readiness (DR, security ops, on-prem packaging) | NEEDS IMPLEMENTATION — Part 19/Part 17's standing practices and Part 2's on-prem package are specified but not built |
| Testing readiness | NEEDS IMPLEMENTATION — Part 23's matrix and Part 24's traceability exist as specifications; the test suites themselves are Phase-by-Phase deliverables, not yet written |
| Regulatory/compliance readiness | NEEDS DECISION — Business Decision 1 above; architecture is compliance-shaped, not compliance-proven |
| Commercial/licensing readiness | READY (design) / NEEDS DECISION — the mechanism is complete; the actual numbers populating the Plan Registry are Business Decision 6 |
| Traceability completeness | READY — the AI Performance Monitoring governance capability (master spec §1.1) previously had no named owner or Part 24 row; this is now resolved (Part 18's Metrics instrument is the explicit, stated owner, and Part 24 carries its own traceability row). No other named capability in the master spec was found to lack a corresponding Part 1 component and Part 24 row as of this revision. |

**No score above is a percentage, and none is inflated to make the project look further along
than it is.** "Architecture completeness: READY" means the design work in Parts 1–24 is genuinely
sufficient to begin Phase 0 without inventing further architecture along the way (per the honest
answer already given in the architecture document's §23.7) — it does not mean any of it has been
built, tested against real adversarial conditions, or validated by a real bank. Those are exactly
the NEEDS TESTING and NEEDS IMPLEMENTATION rows above, and Part 22's phase-by-phase acceptance
criteria are the mechanism by which each one converts to READY over time — not by declaration, by
passing its own stated test.

---

*End of Part 25. This concludes the Master Technical Architecture and Implementation Plan, Parts
1–25.*
