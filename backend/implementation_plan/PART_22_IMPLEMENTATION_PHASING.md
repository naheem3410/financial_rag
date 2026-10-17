# Master Technical Architecture and Implementation Plan
## Part 22 — Implementation Phasing

Derived from the actual dependency graph established across Parts 1–21, not from feature category.
The suggested eight-phase skeleton holds — the dependency graph built by Parts 1–21 supports it
directly, with one adjustment: NPCE/CoVe integration is pulled into Phase 2 rather than kept
separate in Phase 3, since it is largely a multi-tenant-aware port of the already-working research
core rather than net-new invention, and Phase 3 is refocused on the platform-level governance
capabilities that genuinely are new (human escalation, audit hash-chain, model/prompt governance).

Every phase ends with a working, testable increment — nothing here is a "big bang" phase that
produces no runnable system until it completes.

---

## Phase 0 — Foundation / Security Primitives

**Objective.** Establish the primitives every later phase depends on and cannot retrofit cheaply:
secrets handling, tamper-evident audit, and CI-enforced security discipline from the first commit.

**Components built.** Secrets Management (Part 1 §2.29 — reference-only credential storage, no
raw secret ever in application code or config); Audit Service's hash-chain mechanism (Part 14, the
architecture decision, with the storage-backend implementation choice made here) — **provisioned
on infrastructure physically separate from the general Relational Database from this first
commit** (Part 19's updated default), since retrofitting that separation after real audit history
accumulates is the specific, named risk this phase exists to avoid; a minimal API Gateway (Part 1
§2.2) sufficient to route to a health-check service; CI pipeline with SAST, dependency scanning,
and the schema-linter check that fails a build introducing a tenant-scoped table without an RLS
policy (Part 5, summary rule 1).

**Database work.** `audit_events`, `audit_chain` (Part 5 §9); the connection/credential pattern for
every future service's database access (service-level credentials, never a shared superuser, Part
3 Boundary 5).

**APIs.** None client-facing yet — this phase is internal infrastructure.

**Security work.** The full Security Operations program's CI-integrated pieces (Part 17): SAST,
dependency/container scanning, SBOM generation wired into the build pipeline now, not bolted on
later.

**Tests.** Unit tests for hash-chain computation and verification (Part 14 §3); a CI test that
deliberately introduces a tenant-scoped table without RLS and asserts the build fails (proving the
schema-linter works, not just exists).

**Acceptance criteria.** `verify_chain()` correctly detects a tampered record in a test fixture;
no secret value is discoverable in any log, config file, or source-controlled artifact (verified
by a secret-scanning tool run against the codebase).

**Dependencies.** None — this is the root of the dependency graph.

**Deliverables.** A running Secrets Management service, a running Audit Service, a CI pipeline
enforcing the platform's non-negotiable code-level invariants.

---

## Phase 1 — Identity / Tenant / Platform Core

**Objective.** Every subsequent phase needs a real tenant, a real authenticated identity, and a
real authorization decision to build against — this phase makes those real rather than stubbed.

**Components built.** Tenant Service (Part 1 §2.7, Part 5 §1 `tenants`); the multi-tenant schema
pattern with RLS applied to every table introduced from here forward; User/Session Service (Part 1
§2.8); Authorization Engine + Policy Engine (Part 1 §2.6/§2.31, RBAC baseline first, ABAC/PBAC
scaffolding present but not yet exercised by a real module); Authentication Service (Part 1 §2.5);
Identity/IAM Integration Layer (Part 1 §2.3) supporting at minimum OIDC federation and the
platform-managed identity fallback; Customer Authentication Flow Engine (Part 1 §2.4) with the
full closed primitive set implemented, exercised initially against a synthesized mock bank
verification endpoint (Principle 8); the admin bootstrap credential mechanism (Part 15 is not yet
built, but the bootstrap credential issuance itself is needed here to create the first Bank Admin
for testing).

**Database work.** `users`, `roles`, `permissions`, `user_roles`, `policies`, `sessions`,
`customers`, `customer_accounts` (Part 5 §1–§2).

**APIs.** `POST /auth/federate/{provider}`, `POST /auth/token/refresh`, `POST /auth/session/revoke`,
`POST /auth/customer/flow/{tenant_id}/start`, `POST /auth/customer/flow/{flow_id}/step` (Part 20).

**Security work.** The full identity-attack control set from Part 17 (rate limiting on auth
endpoints, MFA support, lockout); the SecurityContext object (Part 4 §1) implemented and enforced
as immutable from this phase forward — every later phase's components receive it, none construct
their own.

**Tests.** The full Part 4 §4 enforcement-table assertions as automated tests (tenant/user/
account/role/permission never LLM-derived — trivially true at this phase since no LLM exists yet,
but the SecurityContext's structural guarantees are tested now so later phases inherit a proven
foundation); a synthesized multi-tenant test fixture proving tenant isolation at the database
layer (Part 3 Boundary 4) before any real customer or document data exists to leak.

**Acceptance criteria.** Two synthesized tenants can be created, each with their own users, and a
query attempting cross-tenant access is denied at the database layer even when the application-
layer check is deliberately bypassed in a test (proving RLS, not just application discipline).

**Dependencies.** Phase 0 (Secrets Management for credential storage, Audit Service for logging
every auth event from day one).

**Deliverables.** A working multi-tenant identity and authorization platform with no RAG/AI
capability yet — genuinely shippable as an internal milestone, testable end-to-end for login,
session, and permission-denial behavior.

---

## Phase 2 — Data / Document / RAG Foundation

**Objective.** Port and multi-tenant-ize the existing research core's faithfulness pipeline
(NPCE, CoVe, the numeric/period-verbatim guards, the three-index invariant) onto the Phase 1
identity/tenant foundation, and build the ingestion hardening the research core never needed
against a single-tenant academic corpus.

**Components built.** Document Ingestion with full hardening (Part 12 — malware scanning, MIME
validation, macro handling, duplicate detection, versioning); OCR (Part 1 §2.34, now genuinely
exercised against synthesized scanned fixtures per architecture §12.1); the tenant_id schema
amendment to the Proposition record (Part 5 §4, the deliberate versioned change to the previously
frozen §5.1 contract); Vector/Sparse/Metadata stores with tenant namespace isolation (Part 1
§2.36–§2.37); Retrieval with the authorization pre-filter (Part 6 §3 — this is where retrieval-
time authorization becomes real, not merely designed); Reranking; the Proposition/Evidence Layer
including the FFF Rewriter and Fact-set/Query Pairer; LLM Gateway (Part 1 §2.16) built on the
OpenAI Agents SDK (architecture §2.2) with the call-site registry (§2.6) seeded with the six
inherited call sites; Guardrails (numeric-verbatim, period-verbatim, operand-verbatim); PII/
Tokenization Service (Part 13) — wired in now since real documents and real queries begin flowing
in this phase; Confidence Engine (Part 1 §2.20, composite scoring, platform-floor enforcement);
Calculation Engine (Part 1 §2.21, the revived Program-of-Thoughts module with its starter formula
registry); Query Processing including NPCE's collision detection, slot parsing, and the
deterministic Query Router (Part 1 §2.5/§2.12); CoVe Orchestrator ported from the research core
with the multi-tenant SecurityContext threaded through it.

**Database work.** `documents`, `document_versions`, `document_chunks`, `propositions` (amended),
`embeddings`, `classifications`, `retention_policies`, `legal_holds` (Part 5 §4, §10).

**APIs.** None new client-facing beyond what's needed for internal testing — this phase's surface
is exercised through Phase 5's module APIs once they exist; a minimal internal query endpoint is
sufficient for this phase's own acceptance testing.

**Security work.** The retrieval-authorization enforcement sequence (Part 6 §3) tested
adversarially now, before any module is built on top of it; the ingestion hardening controls
(Part 12) tested against synthesized malicious/malformed fixtures (Principle 8).

**Tests.** The inherited research core's full acceptance suite (the eleven tests from
`tests/test_acceptance.py`, re-run against the multi-tenant schema); the three-index invariant
post-build assertion (Part 6 §6) as an automated release gate; a synthesized two-tenant retrieval
test proving Part 6 §3's pre-filter sequence — a query under Tenant A's SecurityContext never
returns a Tenant B proposition, verified directly, not inferred.

**Acceptance criteria.** The existing FinanceBench-Collision evaluation (architecture §16.1)
reproduces its prior single-tenant results after the multi-tenant schema amendment — proving the
`tenant_id` addition did not silently change CoVe's numeric comparison behavior, the specific risk
the architecture document flagged when approving that schema change (architecture §4.2).

**Dependencies.** Phase 1 (SecurityContext, Authorization Engine, Tenant Service).

**Deliverables.** A working, multi-tenant, faithfulness-verified RAG core — the single biggest
phase, since it is where the research project's proven results get carried forward rather than
re-derived, now under the platform's identity and tenant model.

---

## Phase 3 — Governance / Verification / Audit (Platform-Level)

**Objective.** Build the governance capabilities that go beyond what the single-tenant research
core needed: human oversight, full audit wiring across every service (not just the RAG core),
and the model/prompt/configuration governance lifecycle.

**Components built.** Human Escalation Service (Part 1 §2.26) with the multi-factor trigger,
routing/reassignment chain, fail-closed timeout, and reviewer self-approval prevention (Part 5 §8
`approvals` constraint); Confidence Engine's two-level threshold (platform floor + tenant policy)
now genuinely enforced against Phase 2's real confidence signals; Configuration Service (Part 1
§2.30, Part 5 §11) with the Draft → Validation → Evaluation → Approval → Production → Rollback
lifecycle (Part 16) applied first to Model Registry and Prompt Registry (Part 1 §2.17–§2.18);
Observability wiring (Part 18) — Langfuse integration with redaction-before-export, distinct from
the Audit Service's own tamper-evident trail; the Audit Service (built in Phase 0) is now wired as
a required dependency of every service built in Phases 1–2, retroactively verified — no service
from earlier phases is allowed to proceed into Phase 4 without its security-relevant writes
confirmed to be audited.

**Database work.** `human_escalations`, `approvals`, `models`, `model_versions`, `prompts`,
`prompt_versions`, `configuration_versions` (Part 5 §8, §11).

**APIs.** `POST /escalations/{id}/approve`, `POST /escalations/{id}/reject` (Part 20).

**Security work.** The full Part 9's audit-adjacent guarantee — every escalation decision, model
approval, and configuration change auditable and traceable to a specific version, tested now
before any Controlled module (Phase 5) begins relying on escalation behaving correctly under real
conditions.

**Tests.** The fail-closed timeout test (no response ≠ approval, Part 5 §8/architecture §9.3) as
an automated test with a simulated non-responding reviewer; the reviewer self-approval rejection
test (Part 5 §8's database constraint, verified to actually reject at the database level, not only
the application level).

**Acceptance criteria.** An escalation that times out with no human response resolves to the
tenant's configured safe-refusal/pending state, never an automatic approval, verified by test, not
by code review alone.

**Dependencies.** Phase 2 (Confidence Engine's real signals to escalate on), Phase 0 (Audit
Service).

**Deliverables.** A governance layer capable of supervising Phase 2's RAG core in production —
this is the point at which the platform is honestly describable as having AI Governance, not just
a faithfulness-tuned RAG pipeline.

---

## Phase 4 — Connector / MCP / Integration Framework

**Objective.** Build the eleven-dimension Connector Framework and the Tool/MCP Gateway, proven
against synthesized bank systems before any real bank connection is attempted.

**Components built.** Tool/MCP Gateway (Part 1 §2.23, Part 10 §3) with the object-level
authorization check independent of LLM/agent input; Connector Framework (Part 1 §2.24, Part 10 §1)
— the full `ConnectorProfile` schema and plugin registries for auth and encryption strategies;
per-tenant MCP server instance deployment (architecture §2.4/§10.11); the Connector Onboarding
Wizard (Part 1 §2.43, the Bank Admin Portal's first real functional section); Transaction
Architecture (Part 11) — the full state machine, idempotency-key discipline, reconciliation flow;
resilience (circuit breaker, backoff) on every connector call.

**Database work.** `connectors`, `connector_credentials`, `tools`, `tool_permissions`,
`transaction_intents`, `transaction_operations` (Part 5 §5, §7).

**APIs.** `POST /admin/{tenant_id}/connectors`, `POST /admin/{tenant_id}/connectors/{id}/test`,
`POST /admin/{tenant_id}/connectors/{id}/versions/{version}/activate` (Part 20).

**Security work.** The full SSRF control (Part 17 — no arbitrary URL ever accepted from LLM/user
input); the idempotency-key duplicate-execution-prevention test (Part 11 §3); the response-schema-
validation control against a deliberately malformed synthesized connector response (Part 17's
MCP-compromise control, tested now rather than assumed).

**Tests.** Contract tests against synthesized mock endpoints covering multiple genuinely different
auth/encryption/transport combinations (basic+REST, OAuth2+mTLS+SOAP, a bank-middleware-indirected
variant, **and an SMS/OTP gateway connector specifically** — a near-universal integration every
customer-facing deployment needs, and one the Customer Authentication Flow Engine (Phase 1)
depends on for a real (non-mocked) OTP delivery path before AI Banking Assistant (Phase 5) can go
live against a real bank) — proving the framework handles heterogeneity, not just one happy path
(Principle 8, restated from the earlier design conversation); the reconciliation-flow test
simulating a timeout followed by a status query resolving to each of SUCCESS/FAILED/still-UNKNOWN
(Part 11 §4).

**Acceptance criteria.** A synthesized "Bank X" connector can be configured entirely through the
onboarding wizard's UI/API, tested, and activated with zero application code changes — proving
architecture §10.1's "configured without modifying the core platform" claim empirically, not just
by design intention.

**Dependencies.** Phase 1 (SecurityContext, Secrets Management for credential references), Phase 3
(Configuration Service's versioned lifecycle, applied to connector definitions).

**Deliverables.** A connector framework proven against realistic synthesized heterogeneity — ready
for a real bank's CBS as soon as one is available, without further framework-level engineering.

---

## Phase 5 — Controlled Modules (MVP Set)

**Objective.** Ship the four Controlled-mode MVP modules defined in Part 21, each exercising a
distinct combination of the infrastructure built in Phases 1–4.

**Components built.** Document Intelligence's Phase 5 work is **production-integration polish
only, not a re-port** — Phase 2 already completed the substantive work (multi-tenant schema
amendment, three-index invariant, NPCE/CoVe porting, FinanceBench-Collision regression proof).
What Phase 5 specifically adds for this module: wiring it into the Conversation Service (Part 1
§2.9) and the module-entitlement check (`feature_entitlements`, Part 5 §12) that every other
module already needs, so it is reachable through the same `POST /conversations`/
`POST /conversations/{id}/messages` surface as the other three MVP modules rather than only
through Phase 2's internal test harness — a distinct, smaller task from Phase 2's core port, named
explicitly here to prevent a build team from either re-doing Phase 2's work or leaving this
integration step undone between the two phases. Enterprise Knowledge Assistant (the
lowest-friction net-new module, first to ship given minimal new infrastructure needs); AI Banking
Assistant (the first module to exercise the Customer Authentication Flow Engine and CBS connectors
end-to-end in production conditions, now including a real SMS/OTP gateway connector per Phase 4's
addition above rather than the synthesized mock used in Phase 1); Compliance Assistant (the first
module to exercise ABAC's "same role, different visibility" requirement under real load).

**Database work.** Module-specific configuration rows in `feature_entitlements` (Part 5 §12);
no new core schema — this phase consumes Phases 1–4's schema, it does not extend it structurally.

**APIs.** `POST /conversations`, `POST /conversations/{id}/messages`,
`GET /conversations/{id}/messages`, `DELETE /conversations/{id}` (Part 20) — the first
client-facing conversational surface.

**Security work.** The full conversation-isolation adversarial suite (Part 7 §10) run against real
(multi-module) traffic for the first time; the account-IDOR and cross-customer-leakage adversarial
tests specific to AI Banking Assistant (Part 21 §3, item 13).

**Tests.** Per-module synthesized golden datasets (architecture §16.7) for Enterprise Knowledge
Assistant and Compliance Assistant (no existing benchmark applies); the FinanceBench-Collision
three-arm comparison, now run through the full platform stack rather than the research
codebase directly, confirming no regression from the additional layers.

**Acceptance criteria.** Each of the four modules passes its own Part 21 evaluation-metrics
criterion and its share of the adversarial suite (Part 23) with zero disclosure on every
cross-boundary attempt.

**Dependencies.** Phases 1–4 in full — this phase is purely integrative, introducing no new
platform-level component.

**Deliverables.** The first genuinely customer- and employee-facing product surface — this is the
platform's first real commercial pilot candidate.

---

## Phase 6 — Agentic Capabilities

**Objective.** Build the Agentic Research Mode lane and ship Regulatory Intelligence, the MVP
set's one Agentic-mode module.

**Components built.** Agent Orchestrator (Part 1 §2.22) with the full execution-budget enforcement
(Part 9); the module-gate mechanism proven to structurally prevent a Controlled module from ever
reaching this pipeline (Part 9 §5); Regulatory Intelligence itself (Part 21 §5).

**Database work.** `agent_tasks`, `agent_steps` (Part 5 §6).

**APIs.** No new foundational API — Agentic-mode tasks are created through the same
`POST /conversations/{id}/messages` shape, routed internally to the Agent Orchestrator based on
the module's static mode assignment (Part 20's existing endpoint, extended in behavior, not in
contract).

**Security work.** The full agent-safety adversarial suite (Part 23): tool misuse, tool loops,
cost runaway, unauthorized action attempts, trajectory correctness — tested against synthesized
adversarial scenarios before this module is exposed to real users.

**Tests.** The stuck-agent watchdog test (Part 9 §3) — a deliberately hung planning loop is
confirmed to be terminated by the independent watchdog, not only by the orchestrator's own
(potentially stuck) check; the module-gate structural test confirming no code path promotes a
Controlled-mode conversation into this pipeline (Part 9 §5) regardless of adversarial query
content.

**Acceptance criteria.** The pre-registered prediction pattern from the research core's own
methodology (§8A.7 of the original architecture document) is applied here too: before Regulatory
Intelligence's real evaluation run, expected trajectory shapes and cost ranges are recorded, so the
actual result can be checked against a genuine prediction rather than rationalized after the fact.

**Dependencies.** Phases 1–5 (this lane reuses the identical retrieval/governance/connector core,
differing only in orchestration).

**Deliverables.** The platform's first Agentic-mode module, and — more importantly — proof that
the two-lane architecture (architecture §2.1) holds under real conditions, not just in design.

---

## Phase 7 — Enterprise Hardening / Deployment

**Objective.** Complete the three deployment models, disaster recovery, the standing security
operations program, and the full commercial/licensing mechanism — the work that turns a working
product into one sellable to a real bank under contract.

**Components built.** All three deployment models fully packaged (Part 2) — the on-prem
deployment artifact (container images, deployment charts, migration scripts, signed license,
documentation, architecture §22.10); Backup/DR fully wired per Part 19's per-component RPO/RTO
table, with automated recovery-drill tooling; the standing Security Operations program (Part 17)
moved from CI-only (Phase 0) to a full operational practice (penetration testing cadence,
incident-response runbook, credential-compromise runbook); Licensing/Plan Registry (Part 1
§2.41–§2.42) with the signed-entitlement mechanism and the fully configurable pricing table
(architecture §22.4); Admin & Break-Glass (Part 15) completed in full, including the Platform
Admin Console's own internal least-privilege model; tenant onboarding and offboarding procedures
(architecture §23.3) automated where the architecture allows.

**Database work.** `licenses`, `plans`, `feature_entitlements` (Part 5 §12) — populated with the
actual Plan Registry data; `configuration_versions` extended to cover the sensitive-domain
two-person-approval workflow (Part 15 §A) for Bank Admin and Platform Admin actions alike.

**APIs.** `POST /licenses/issue`, `GET /licenses/{tenant_id}/verify`,
`POST /platform-admin/break-glass/request`, `GET /platform-admin/break-glass/{id}/status`,
`POST /tenant-admin/{tenant_id}/break-glass/{id}/consent`,
`GET /tenant-admin/{tenant_id}/break-glass` (Part 20, Part 15).

**Security work.** A full third-party penetration test against the complete platform (all four
modules, all three deployment models where feasible to test); the recovery-drill sequence (Part
19) executed for real against a staging environment for every stateful component.

**Tests.** Part 23's complete test matrix, run in full for the first time as one integrated
suite rather than per-phase subsets; the on-prem deployment package tested by actually deploying
it into an isolated environment simulating a bank's own network, not merely reviewed on paper.

**Acceptance criteria.** Part 25's Final Build Readiness Review can be completed with every item
in the "Architecture Frozen" and "MVP Scope" categories marked genuinely READY, not
NEEDS-IMPLEMENTATION or NEEDS-TESTING.

**Dependencies.** All prior phases.

**Deliverables.** A platform ready for a genuine first bank pilot under contract — the true end of
"MVP" in the commercial sense, not merely the technical sense reached at the end of Phase 5.

---

## Cross-Phase Rule

**No phase's acceptance criteria may be waived to unblock the next phase.** If Phase 2's
three-index invariant check, or Phase 3's fail-closed escalation test, or Phase 4's connector
heterogeneity proof fails, the dependent phase does not begin on schedule — the dependency graph
above is real, not aspirational, and skipping a phase's proof obligation reintroduces exactly the
kind of unverified assumption the architecture-completeness review (architecture §23) was
conducted to eliminate.

---

*End of Part 22. Part 23 (Test Strategy) follows immediately.*
