# Financial Institution AI Platform — Enterprise Architecture & Commercial Plan
## Master Specification — Pre-Implementation, Revision 1

**Status of this document.** This is the authoritative, implementation-ready specification for the
**Financial Institution AI Platform** — the multi-tenant enterprise product that the existing
`financial_rag` research codebase (see `Financial_RAG_System_Architecture.md`, `CLAUDE.md`,
`PROMPT_SPECIFICATIONS.md`, `Findings_and_Conclusion.md`) becomes the seed of. This document does
not replace those files — the existing codebase's NPCE + CoVe faithfulness core, its acceptance
tests, and its evaluation results remain valid and become the **Document Intelligence** module's
implementation inside **Banking Operations**, and its governance primitives (numeric guard, CoVe,
citation binder, abstention gate) become the first working draft of the platform's **AI Governance**
layer. Everything in this document is new work layered around and above that core.

**Purpose.** This document is written to be handed to an engineering team — human or AI agent —
with the expectation that every decision, constraint, and mechanism described here is followed
exactly, with nothing omitted, no matter how small. Where a specific number (a threshold, a limit,
a percentage) is given, it is a **starting default**, explicitly configurable, never a hard
requirement unless stated as a **platform floor**. Where a decision is described as
**[implementation choice]**, it is swappable behind its stated interface; everything else is
prescribed.

**How to read this document.** Section 0 states the governing principles that every other section
must be consistent with. Sections 1–24 are the technical and commercial architecture, organized in
the order this plan was developed and reviewed. Section 25 is the full architecture-completeness
audit and its resolutions. Section 26 is the concept glossary. Section 27 lists what remains
deliberately deferred. Phasing (the build order that turns this into shippable milestones) is a
separate, subsequent document.

---

# 0. Governing Principles

These principles are load-bearing. No component, module, or configuration option may violate them.

**Principle 1 — Constrained LLM surface (inherited from the research core).**
> An LLM is used only where linguistic generation or semantic judgment is genuinely required.
> Every numeric, structural, and provenance operation is deterministic code.

**Principle 2 — Authoritative systems remain authoritative.**
> The AI platform can propose, retrieve, reason, explain, and orchestrate. It is never the
> authority that decides who owns data, who may access it, or whether a transaction is
> authorized. The bank's own identity, authorization, and transaction-control systems retain
> that authority at all times.

**Principle 3 — Three-way separation of concerns.**
> Authentication answers who someone is. Authorization answers what they can do. The AI operates
> only inside whatever those two have already decided. It never gets to answer either question
> itself.

**Principle 4 — No data enters an LLM context merely because it exists in the platform.**
> Every piece of content — a document, a conversation turn, a tool result, a memory record —
> must pass tenant, identity, authorization, scope, classification, and policy checks before it
> is allowed into any LLM-facing context, regardless of where it came from or how it got into the
> platform.

**Principle 5 — Fail-closed, always.**
> No response is never treated as approval. A timeout is never treated as permission. When the
> system cannot establish sufficient evidence or sufficient authorization, it defaults to a safe
> refusal or a pending state — never to guessing, and never to proceeding.

**Principle 6 — Configuration over code, everywhere a bank-specific or commercial decision is made.**
> Tenant infrastructure choice, connector behavior, pricing, entitlements, and deployment topology
> are all configuration, resolved by a factory or registry at runtime. A new bank, a new
> connector, a new price, or a new deployment shape must never require an application code change.

**Principle 7 — Defense in depth on every isolation boundary.**
> Tenant isolation, customer isolation, and employee isolation are each enforced at more than one
> independent layer (application, database, vector store, cache, configuration store). A single
> missed filter in one layer must never be sufficient to cause a leak.

**Principle 8 — Test everything; synthesize data where none exists.**
> No functionality ships untested. Where no realistic data exists for a domain (a bank's CBS, a
> scanned KYC form, a multi-year policy history), realistic synthetic fixtures are built
> deliberately so the functionality is proven, not assumed.

---

# 1. Product Scope

## 1.1 The five pillars

```
Financial Institution AI Platform
│
├── Customer Experience     (AI Banking Assistant, Account Opening, KYC Guidance,
│                             Product Discovery, Customer Support, Complaint Assistance,
│                             Financial Education)
├── Employee Experience     (Enterprise Knowledge Assistant, HR Assistant, IT Assistant,
│                             Operations Assistant, Customer-Service Copilot,
│                             Training/Onboarding Assistant)
├── Banking Operations      (Credit Decision Support, Compliance Assistant, AML/KYC Assistant,
│                             Risk Assistant, Fraud Investigation Support,
│                             Regulatory Intelligence, Document Intelligence)
├── Management               (Executive Knowledge Assistant, Policy Intelligence,
│                             Regulatory Impact Analysis, Operational Insights,
│                             Enterprise Search, Decision Support)
└── AI Governance             (Hallucination Detection, Citation Verification,
                                Confidence Scoring, Audit Trails, Access Control,
                                Human Escalation, AI Performance Monitoring)
```

AI Governance is **not a sixth peer module** — it is a cross-cutting layer every other pillar is
built on top of, load-bearing from the first line of code, not retrofitted.

## 1.2 Non-negotiable cross-cutting requirements

Accuracy, consistency, and explainability; data security, privacy, and compliance; scalability and
performance; integration and interoperability; customization and domain adaptation. Every section
below exists to satisfy one or more of these.

## 1.3 Positioning

The platform is **"a controlled AI operating platform for financial institutions,"** not "a
financial RAG product." The RAG/NPCE/CoVe core is the engine; AI Governance, security, and the
connector framework are the differentiator and the moat.

---

# 2. Orchestration Architecture

## 2.1 Two lanes, one shared core

- **Fixed Faithfulness Pipeline** — the evolved form of today's six-call-site pipeline. Used by
  Customer Experience, Employee Experience (most modules), Banking Operations' Credit/Compliance/
  AML/Risk, and any module requiring predictable latency, predictable cost, and a fully auditable
  fixed path.
- **Agentic Research Mode** — multi-step, tool-using orchestration via the OpenAI Agents SDK's
  native agentic loop. Used only by modules explicitly assigned to it (§2.3).

Both lanes sit on top of the same retrieval core, the same governance layer, and the same
confidence/citation infrastructure. The difference is who decides the next step: our deterministic
orchestrator code (fixed lane) or the agent itself (agentic lane).

## 2.2 OpenAI Agents SDK adoption

- Every one of the platform's LLM call sites is implemented as an Agents SDK `Agent` definition:
  instructions (from the call site's prompt specification), a Pydantic `output_type`, and a
  pinned model/temperature/seed exactly as the existing `config/schema.py` per-call-site pattern
  already does.
- **Fixed-lane agents receive no tools that would trigger a second turn.** One `Runner.run` call
  per invocation — same single-call auditability as today, implemented on better infrastructure.
- **Multi-provider swap is preserved.** OpenAI models run natively on the SDK; Anthropic runs via
  its LiteLLM-backed model integration; Ollama runs via its OpenAI-compatible local endpoint
  pointed at the SDK's chat-completions model class. The existing `config/schema.py` per-call-site
  provider field and `tests/test_provider_swap.py`'s guarantee both carry forward unchanged.
- **Agents SDK tracing feeds the Audit Trail.** Its built-in trace spans (latency, tokens, tool
  calls) are exported through the same pipeline as Langfuse (§17), not hand-rolled separately.

## 2.3 Module-gated orchestration mode

Orchestration mode is a property of the **module**, assigned at design/deployment time. It is
**never inferred from a query's wording, and a module can never escalate itself into a mode it was
not assigned.**

| Module | Mode |
|---|---|
| Customer Banking Assistant, Account Opening, KYC Guidance, Product Discovery, Support, Complaints, Financial Education | Controlled |
| Enterprise Knowledge Assistant, HR Assistant, IT Assistant, Operations Assistant, Customer-Service Copilot, Training | Controlled |
| Credit Decision Support, Compliance Assistant, AML/KYC Assistant, Risk Assistant, Document Intelligence | Controlled |
| Regulatory Intelligence, Fraud Investigation Support | Agentic-allowed |
| Executive Knowledge Assistant, Policy Intelligence, Regulatory Impact Analysis, Operational Insights, Enterprise Search, Decision Support | Agentic-allowed |

## 2.4 MCP as the integration seam

- Every external system (CBS, HR, CRM, fraud, credit bureau, transaction monitoring) is exposed as
  a narrow, task-specific MCP server (`get_account_balance`, `get_kyc_status`,
  `create_service_ticket`) — never a generic "run query" surface.
- Internal retrieval is itself exposed as an MCP tool (`search_knowledge_base`). In the fixed
  lane, it is invoked directly by orchestrator code — the agent never decides whether or how to
  call it. In the agentic lane, it is a tool the agent actively chooses to call, and may call more
  than once.
- MCP server instances are **deployed per tenant**, never shared across banks, so a compromise of
  one server is contained to a single tenant (§25, item: compromised-MCP blast radius).
- Every tool response is schema-validated against its expected Pydantic contract before being
  trusted, and treated as **untrusted data**, subject to the same "never an instruction" rule as
  retrieved documents (§10.1).

## 2.5 Query Router

Extends the existing `online/analysis/query_analyzer.py` collision detector into a deterministic,
embedding-assisted router that — **only within whichever lane its module already belongs to** —
selects one of:

1. Direct answer (single fact, no collision)
2. Query rewrite/expansion (ambiguous or under-specified, no collision)
3. Sub-query decomposition without full NPCE placeholder machinery (multiple unrelated asks, no
   collision risk)
4. Full NPCE + CoVe (collision detected, or the module/tier requires it regardless of collision
   score)

The router itself is deterministic-plus-embedding-based, not a cheap LLM classifier — this
preserves Principle 1 as the platform grows.

## 2.6 Call-site registry

The research core's "exactly six LLM call sites" rule generalizes into a **documented, enumerated,
tested call-site registry**. Any new LLM touchpoint required by a new module must be added to this
registry with an explicit linguistic/semantic justification and is reviewed before being enabled —
never added silently. The registry, as of this document, contains the original six
(`PROMPT_SPECIFICATIONS.md`) plus the Calculation Planner (§2.7).

## 2.7 Calculation module (Program-of-Thoughts, revived)

- **New call site: Calculation Planner.** The LLM's only job is selecting which named operation
  from a versioned, code-defined formula registry applies, and which retrieved proposition IDs
  feed it. It never computes the number itself and never emits a digit.
- **Formula registry starter set:** percentage change, ratio, CAGR, simple growth rate, sourced
  currency conversion (using a verified, sourced FX rate as an input, never assumed), basic
  interest. Any operation not in the registry, or any question the registry cannot disambiguate
  between valid conventions for, causes **abstention by design**. The registry grows deliberately,
  reviewed before each addition, same discipline as §2.6.
- **Operand-verbatim guard.** Every operand in a calculation plan must trace to a real, existing
  `retrieved` or already-verified `derived` proposition ID. A plan referencing a non-existent fact
  is rejected before it runs.
- **Derived facts get `provenance_tier: derived`** (already schema-reserved in the frozen
  Proposition contract) and record the operation and its input proposition IDs.
- **Verification is stronger for derived facts than for retrieved ones** — a derived claim can be
  checked by literally re-running the same deterministic operation and comparing outputs, rather
  than requiring independent LLM re-answering.
- Available to any module that needs it (Credit, Risk, Executive Insights) via the shared
  retrieval/generation core, in both lanes: a pipeline step in the fixed lane, an MCP tool in the
  agentic lane.

## 2.8 Cost and latency budgeting

Every call site (and, in the agentic lane, every task) carries a configurable max-latency and
max-cost budget in config, enforced via Agents SDK tracing data. Breaching a budget triggers an
automatic cheaper fallback (skip CoVe, cap agentic tool-call count) rather than an uncontrolled
overrun.

## 2.9 Observability — Langfuse

Langfuse is the aggregation layer over the Agents SDK's native trace spans — one exporter feeding
audit trails, per-session cost tracking, and prompt/response inspection. See §17 for tenant
scoping and redaction requirements on this pipeline.

---

# 3. Modular RAG & Component Interfaces

The existing Protocol interfaces (`contracts/interfaces.py`) are the backbone of the platform's
modularity, extended from component-level swapping to **module-level and tenant-level** swapping:

```python
class Retriever(Protocol): ...
class Reranker(Protocol): ...
class ContextCurator(Protocol): ...   # FFF rewriter; NPCE-off = pass-through
class QueryTransformer(Protocol): ... # splitter; NPCE-off = identity
class Verifier(Protocol): ...         # CoVe; CoVe-off = pass-through
class Embedder(Protocol): ...         # shared, not cell-toggled
```

- **Toggling an experiment cell / product module swaps the implementation bound to
  `QueryTransformer` and `Verifier` — no other code changes.**
- **Module-level:** KYC Guidance can be configured with a stricter `Verifier` than Product
  Discovery; Credit Decision Support can use a `Reranker` checkpoint tuned on credit
  documentation.
- **Tenant-level:** one bank can swap `Embedder` for a domain-fine-tuned checkpoint without
  touching any other tenant's configuration.

---

# 4. Multi-Tenancy & Data Model

## 4.1 Tenancy model

**The platform is multi-tenant by design, supporting three deployment shapes from one codebase:**
shared SaaS, dedicated (isolated infrastructure, either our cloud account or the bank's own),
and on-premises (§24.6).

## 4.2 `tenant_id` as a first-class field

Every core record carries `tenant_id`: propositions, documents, users, conversations, cache
entries, audit logs, citations, connector configurations, and licensing/entitlement records.

**Note on the frozen Proposition schema.** The existing research codebase's §5.1 Proposition
record is documented as a frozen contract that "must never change." Adding `tenant_id` is a
**deliberate, versioned amendment** to that contract, not a silent violation of it — it must be
recorded as such, with CoVe's numeric comparison logic re-verified against the amended schema
before this change ships.

## 4.3 Three-index invariant, extended

Dense index, sparse index, and metadata store now key on **`(tenant_id, proposition_id)`** —
the same object, the same tenant, across all three indexes.

## 4.4 Defense-in-depth tenant isolation (Principle 7 applied)

Isolation is enforced at **every** layer independently, not as a single application-level filter:

| Layer | Mechanism |
|---|---|
| Application | Tenant context resolved once per request, threaded through every call |
| Database | Row-level security policies tied to the connection's tenant context |
| Vector store | Physically or logically separate namespaces per tenant |
| Object storage | Separate prefixes/buckets per tenant |
| Cache | Tenant is part of every cache key (§14.5) |
| Configuration store | Same database-level enforcement as runtime data — a bank admin can never see another tenant's connector settings, even via URL manipulation |
| Logs / traces / metrics | Tenant-tagged and access-scoped (§17) |
| Backups | Tenant-independent restore (§20) |

## 4.5 Deployment definitions

- **Shared SaaS** — we operate the infrastructure entirely; tenant isolation via the layers above.
- **Dedicated** — isolated infrastructure, either in our cloud account or the bank's own, per
  contract; same tenant model underneath.
- **On-premises** — the **entire platform** (app layer, data layer, connector framework) deploys
  inside the bank's own infrastructure. See §24.6 for the full definition and IP boundary.

---

# 5. Infrastructure Abstraction

## 5.1 Ports-and-adapters, generalized from the LLM routing pattern

One interface per infra concern, each resolved from config by a factory, never wired in code:
`VectorStore`, `SparseIndex`, `MetadataStore`, `ObjectStore`, `MessageQueue`/`EventBus`, `Cache`,
`SecretsManager`.

## 5.2 Adapter pairs

pgvector/Qdrant self-hosted vs. managed vector search · RabbitMQ vs. SQS/EventBridge · MinIO vs.
S3/Blob Storage · local Redis vs. managed cache · a local vault or `.env` vs. cloud secrets
manager. Same shape everywhere — pick a backend, everything above it doesn't change.

## 5.3 Three deployment profiles from one containerized artifact

Cloud-managed, self-hosted/on-prem, and a lean "conventional hosting" single-VM profile — differing
only in which config file is loaded.

## 5.4 Scalability levers

- Stateless online query workers behind a queue, so ingestion and batch evaluation scale
  independently of live query traffic.
- A semantic cache in front of retrieval for repeated/near-duplicate queries — the single cheapest
  latency/cost win available, subject to the security constraints in §14.
- Per-tenant concurrency and rate limits enforced at the gateway layer, sized so no single
  tenant's traffic spike can starve another's request queue (resource isolation / noisy-neighbor
  protection).

---

# 6. Identity, Authentication & Authorization

## 6.1 The twin principle restated for this section

Authentication answers who someone is. Authorization answers what they can do. The AI operates
only inside what those two have already decided (Principle 3).

## 6.2 Employee/staff identity

- A bank's own IAM (Azure AD/Entra ID, Okta, generic SAML/OIDC) is trusted as the source of
  identity — the platform never stores that bank's employee passwords.
- A **platform-managed identity fallback** exists for banks without enterprise IAM: proper
  password hashing (bcrypt/argon2), MFA, lockout after repeated failed attempts, session expiry.
- A bank's genuinely custom/legacy authentication system gets a custom `IdentityProvider` adapter,
  flagged for its own security review before being enabled for that tenant.
- Identity claims (user ID, groups) are mapped, via a **tenant-configurable mapping table**, to
  platform roles and attributes (e.g., a bank's "Compliance-Lagos" AD group maps to role
  `compliance_officer` + attribute `region=Lagos`).

## 6.3 Customer Authentication Flow Engine

Both employee and customer authentication run through **one Authentication Flow Engine**,
configured differently per population and per bank:

- **Modeled as a declarative state machine**, composed from a **finite, closed set of vetted
  primitives** — never a generic workflow executor capable of arbitrary logic:
  `CollectInput`, `VerifyIdentifier`, `Password`, `PIN`, `OTP`, `MFA`, `OAuth2`, `OIDC`, `SAML`,
  `TokenExchange`, `CertificateAuthentication`, `DeviceBinding`, `SessionCreation`, `TokenRefresh`,
  `Logout`/`Revoke`.
- Employee flows are typically short (federate to the bank's IdP in one step). Customer flows are
  typically longer (collect BVN → verify → OTP → session), assembled from the same primitives.
- **Each step that calls an external bank endpoint is itself a Connector Framework call** (§10),
  with its own transport/auth/encryption/binding — no separate mechanism is invented for
  authentication-flow steps.
- A bank's genuinely proprietary flow gets a new primitive/adapter, **security-reviewed before it
  is made available to that tenant.**
- **The entire flow sits outside the AI/RAG pipeline.** The LLM never sees a BVN, an OTP, an
  access token, or a refresh token — it never improvises what to ask next in a login sequence.
  The AI layer receives control only after the flow completes, handed a resolved identity
  context.
- Rate limiting applies to the whole flow (BVN attempts, OTP attempts, password attempts), keyed
  on **tenant + customer identity + device + IP/network + session + endpoint together**, not IP
  alone.
- OTP delivery stays with the bank's own SMS/email gateway by default; the platform only takes
  this on if a bank explicitly delegates it.
- **Admin bootstrap.** Before a bank's IAM is connected, there is no identity provider to
  authenticate a first admin against. This is solved with a vendor-issued, time-limited,
  MFA-enforced invite credential for exactly one designated first admin, whose only action in that
  session is connecting the bank's real IAM — the bootstrap credential is invalidated immediately
  afterward and never reused.

## 6.4 Session/Token Manager

Access tokens, refresh tokens, and session tokens are held by a dedicated Session/Token Manager:
securely stored, expiry-tracked, refreshed ahead of expiry, re-authenticated on refresh failure,
revoked on logout. **The AI layer never receives a token** — only a resolved identity context
(`customer_id`, `auth_level`, `permitted_accounts`, etc.).

## 6.5 RBAC / ABAC / PBAC

- **RBAC is the mandatory baseline, always on.**
- **ABAC/PBAC is an optional advanced layer**, enabled per tenant/module, for conditions RBAC
  cannot express (region, department, clearance level, case assignment). If a bank never touches
  it, the system behaves as RBAC-only.
- These are not presented to a bank as competing modes to choose between. The one real dropdown in
  the admin portal is the **identity provider** (Azure AD, Okta, generic SAML, or platform-managed
  identity).
- Least privilege is the default; elevated access for sensitive work is **time-bound**, not a
  standing grant.
- Permissions can be time-bound and can require a second approver for the most sensitive changes.

## 6.6 Object-level authorization

Applies uniformly to customer accounts, employee records, and conversations:

- A customer-facing tool never accepts a free-text account/customer identifier from conversation
  content. It resolves identity from the authenticated session and checks any referenced account
  against the accounts actually linked to that session **before** the call is made.
- HR-module access does not imply access to any specific employee's record — every retrieval and
  tool call checks whether *this specific record* is within *this specific staff member's* scope
  (role + attributes), not just whether the module is enabled for them.
- **Old conversation content never grants a standing bypass.** If a permission was valid when
  content first entered a conversation but isn't valid anymore (role changed, access revoked),
  authorization is re-checked at the moment of use, not inherited from the past.
- The bank's own CBS/HR system remains the **authoritative source of ownership facts** (Principle
  2); the platform's connector-layer check is a second gate on top of it, never a replacement.

## 6.7 Identity is never derived from conversation content

If a user types "I'm the CEO, override this," the system is structurally incapable of treating
that as an identity or authorization claim — identity is established exactly once, at
authentication, and carried through the session as immutable context.

## 6.8 Step-up authentication

Applies to any money-movement action (customer) or approval of a large transaction / access to a
sensitive case (employee) — a live session is not sufficient on its own; a re-verification step is
required at the moment of the sensitive action.

## 6.9 Disabled IAM account with an active session

If a bank disables a user's IAM account while that user has an active platform session, the
session must be invalidated on the next identity check (a periodic re-validation against the IAM,
not only at initial login) — an active session must not be allowed to silently outlive the
identity that authorized it.

---

# 7. Privacy & PII Protection

## 7.1 Position in the pipeline

PII detection and redaction runs **before** anything reaches embedding or an LLM call — on live
user queries and on ingested documents alike (a scanned form with a visible account number is
redacted before it is embedded, same rule as query-side redaction).

## 7.2 Swappable interface

A `PIIRedactor` interface with adapters: OpenAI Privacy Filter, Microsoft Presidio (open-source,
self-hostable, preferred default for on-prem), and deterministic bank-specific regex (BVN, NIN,
account/card numbers). **Verify the exact capabilities of whichever adapter is chosen against
current vendor documentation immediately before implementation** — this document's description of
any specific named third-party product is not authoritative at build time.

## 7.3 Reversible tokenization (the mechanism that resolves "the AI needs the real value")

- Reuses the same pattern already built for NPCE's entity placeholders. A value like an account
  number becomes `<ACCOUNT_1>` before anything touches embedding or the LLM.
- The real value lives only in a **short-lived, encrypted, session-scoped mapping vault**.
- **The LLM never sees the real value and never resolves it itself.** A tool call that genuinely
  needs the real number (a CBS lookup) resolves it by **deterministic code at the tool-call
  boundary only**.
- **Deletion of the mapping vault follows the bank's own data-retention policy**, not a platform
  default — this varies by jurisdiction.

## 7.4 Tenant-configurable policy

Each bank configures which PII categories get redacted and its own retention timeline — different
banks, different rules, same mechanism underneath.

## 7.5 Applies everywhere content can leak

Embedding, retrieval, LLM calls, logging, tracing, caching, and analytics all sit **downstream**
of the redaction step — nothing bypasses it by entering through a different channel.

---

# 8. Security

## 8.1 The four gates (mental model for this whole section)

Authentication (bank-owned identity) → Authorization (RBAC/ABAC, enforced at retrieval, not after)
→ Privacy (PII redaction, tenant-configurable) → AI Trust (NPCE/CoVe/citation/confidence) → Audit
(immutable, every hop logged).

## 8.2 Encryption

- Every store (database, vector index, object storage, backups) encrypted at rest; TLS everywhere
  in transit.
- Dedicated and on-prem tenants may hold their own encryption keys (BYOK).
- Payload-level encryption (e.g. AES-GCM) is configured **per connector**, separate from and in
  addition to transport TLS, when a specific bank's endpoint requires it.
- **Key ownership and rotation:** platform-managed keys (DB, object storage, TLS certs) rotate on
  an automated schedule we own; connector secrets rotate per the bank's own policy since they own
  those credentials; **the license-signing private key is the single most sensitive key in the
  system** and lives in a dedicated managed key vault with the strictest access policy of anything
  in the platform; BYOK deployments put rotation entirely in the bank's hands, with the platform
  handling a rotation event gracefully (re-encrypt/re-wrap) without downtime.

## 8.3 Input-side threat detection

Content filtering and embedding-based anomaly detection run **in parallel with retrieval**, not
serially in front of it, so normal-case latency is unaffected. A direct attempt to extract system
instructions is included as a detection signal (§8.5).

## 8.4 Prompt injection defense

- Retrieved documents, uploaded content, and **tool/connector responses** are all structurally
  treated as **data, never as instructions** — enforced the same way CoVe's independence is
  enforced (by what a function is even allowed to accept), not by a prompt asking the model to be
  careful.
- Every tool response is schema-validated against its expected contract before being trusted.

## 8.5 Hidden-prompt exfiltration — the honest answer

The platform does **not** guarantee a system prompt can never be surfaced. It guarantees that
**nothing dangerous is ever placed inside the prompt to begin with** — no credentials, no other
tenant's data, no PII — so even a successful extraction attempt reveals only instructions, never
secrets. A direct "reveal your instructions" pattern is added to the anomaly-detection signal set,
not relied upon to be self-refused by the prompt's own wording.

## 8.6 Behavioral anomaly detection

Distinct from input-content filtering — watches access **patterns** over time (a staff account
suddenly pulling hundreds of customer records, unusual hours, impossible-travel session patterns)
and triggers step-up authentication or a security review, since none of this looks malicious in
the query text itself.

## 8.7 Frontend security baseline

CSRF tokens on state-changing requests; output encoding and CSP against XSS; frame-ancestors
restrictions against clickjacking; uploads scanned and type-validated (§15.3); downloads
authorization-checked at request time. **Every check is enforced server-side** — the frontend is
never the authorization boundary; manipulating it must never bypass a real check.

## 8.8 API security

Every platform-exposed API independently enforces authentication, authorization, tenant binding,
rate limiting, input/output schema validation, and idempotency — an API caller bypassing the UI
must remain exactly as constrained as one going through it.

## 8.9 Security operations program (standing practice, not a one-time review)

Automated dependency/container scanning in CI; SAST on every commit; periodic third-party
penetration testing; an SBOM generated per release; a documented incident-response runbook with
defined severity levels and notification SLAs; a specific credential-compromise runbook
(immediate rotation, revocation, tenant notification within a committed window).

## 8.10 Supply-chain security

Exact dependency pinning platform-wide (already the practice for the embedding model in the
research core, extended everywhere); an SBOM per release; vulnerability scanning on dependencies
and container images before every release. A compromised dependency gets the same blast-radius
thinking as a compromised connector — no single package has a path to secrets or cross-tenant
data by design.

## 8.11 Transaction authorization separation

The AI never executes a transaction. It produces a **transaction intent**. That intent is
submitted to the bank's own authorization pipeline (step-up auth, fraud/risk checks, accept/reject)
and only the bank's own transaction system executes. The platform's write path ends at "intent
submitted," never at "money moved" (Principle 2 applied concretely).

---

# 9. Human-in-the-Loop & Escalation

## 9.1 Triggers (multi-factor, never confidence alone)

Confidence below the tenant's threshold; any write or high-risk action; a policy requirement;
CoVe abstaining on a material claim; a detected PII/security anomaly; an explicit user request for
a human.

## 9.2 Routing

Escalation routes to the correct queue by role and module — a compliance escalation never lands in
the general customer-service queue. Reassignment chain: primary reviewer → secondary → manager,
before failing safe (each hop a routing rule on the same message-queue infrastructure, §5).

## 9.3 Fail-closed, named as one principle

**No response is never treated as approval. Timeout is never treated as permission. Uncertainty
defaults to safe failure.** This is the same abstention principle already governing the AI layer,
extended into the human layer.

## 9.4 Reviewer self-approval prevented

A reviewer can never approve an escalation where they are the original requester or the subject of
the action (enforced by a requester-id/reviewer-id check). The most sensitive categories (large
transactions, HR actions touching the reviewer's own reports) require two independent approvers.

## 9.5 Reviewer context and audit

A reviewer sees the full drafted answer, evidence, confidence, and risk flags before deciding —
never a cold handoff. Every escalation event, approval, rejection, and timeout is logged
immutably against tenant, user, and session.

## 9.6 What the reviewer is authorized to see

A reviewer's access to the escalated conversation/record is itself object-level authorized — being
assigned to the escalation queue does not by itself grant visibility into content the reviewer
would not otherwise be permitted to see.

---

# 10. Connector & Integration Framework

## 10.1 Eleven independent, composable dimensions

Every bank connector (CBS, HR, CRM, fraud, credit bureau, transaction monitoring, and every
authentication-flow step that calls an external endpoint) is defined by a combination across:

1. **Transport** — REST, SOAP/XML, GraphQL, gRPC, WebSocket, bank middleware
2. **Authentication** — Basic, API Key, OAuth2 (client credentials / authorization code / refresh
   token), JWT, mTLS, HMAC, WS-Security, custom (security-reviewed before enablement)
3. **Authorization** — Bearer token, OAuth scopes, roles, permissions, claims, custom (a distinct
   concern from authentication — what a credential grants, not how it was obtained)
4. **Credential acquisition** — static secret, login endpoint, token endpoint, token refresh,
   certificate, key pair, custom handshake
5. **Request binding** — HTTP headers, query parameters, form fields, SOAP headers/envelope, gRPC
   metadata, GraphQL variables
6. **Headers** — static (`X-API-Key`, `X-Channel-ID`), templated, and **generated**
   (`Idempotency-Key`, generated once per logical write and reused on every retry of that same
   request — never regenerated on retry, which would defeat its entire purpose and could let a
   timed-out transfer execute twice)
7. **Encryption** — TLS, mTLS, AES-CBC/GCM, RSA, HMAC signing, JWE/JWS, XML encryption, custom
8. **Request format** — JSON, XML, SOAP envelope, GraphQL query, Protobuf
9. **Session lifecycle** — authenticate, establish session, cache token, refresh token,
   re-authenticate, logout/invalidate
10. **Request lifecycle** — request ID, idempotency, timeout, retry, circuit breaker, backoff
11. **Response handling** — parser (JSON/XML/SOAP/Protobuf), error mapping, normalization into one
    internal shape regardless of what came in

**Auth strategy and binding are separated deliberately:** a credential (bearer token, certificate)
is obtained the same way regardless of transport; only how it's *attached* to a specific protocol's
request (header vs. gRPC metadata vs. SOAP envelope element) changes. A new transport does not
force a new auth strategy — it needs a new binding for strategies that already exist.

## 10.2 Auth and encryption are plugin registries, not hardcoded logic

Common strategies ship built in. A bank with a genuinely proprietary scheme gets **one new plugin
written against the shared interface** — it does not ripple into anything else.

## 10.3 The AI never knows any of this (named principle)

The MCP tool is `get_account_balance(account_reference)` whether the bank underneath is
REST+OAuth2, SOAP+WS-Security, or gRPC+mTLS. Everything in §10.1 lives in the connector engine,
invisible above that line.

## 10.4 Credentials

Structured bundles, not single values — username, password, AES key, IV, salt, client secret,
certificate — each one individually a **reference** into the secrets manager, resolved only at
call time, populated during onboarding through a guided setup, never hand-typed into a config
file, never redisplayed in full after initial entry.

## 10.5 Bank middleware indirection

If a bank refuses direct CBS access, the connector for `get_account_balance` simply points at
their middleware instead — the MCP tool's interface never changes, only what's configured behind
it. This also supports network-level restrictions a bank may require (VPN, private network, IP
allowlist, PrivateLink, mTLS, proxy/gateway) and can operate without any inbound connectivity into
the bank's network.

## 10.6 Connector Capability Manifest

Each connector declares exactly which operations and writes it supports. The platform cannot
attempt anything beyond that manifest, **independent of RBAC** — a second, infrastructure-level
gate on top of the permission layer.

## 10.7 Connector Onboarding Wizard

Part of the unified Bank Admin Portal (§23.1): pick the system, pick transport and auth, add
headers, configure secrets by reference, select which operations are enabled, test the connection,
run contract tests, save. Advanced mode for a connector that doesn't fit the guided flow.

## 10.8 Write operations discipline

- **Default to read-only tools.** Writes require an explicit allowlist per tool per role.
  Money-movement or PII-changing writes require human confirmation and step-up authentication
  (§6.8, §9).
- Only narrow, task-specific tools exist — no generic "run anything" surface.
- **Live/transactional data is never cached or embedded into the index** — fetched fresh at answer
  time. Static knowledge (policies, product docs) goes through offline ingestion; both can combine
  in one answer (e.g., live account status + retrieved policy text) via the existing assembler.

## 10.9 Resilience

Circuit breaker and backoff on every connector call. A failed or unreachable external system
returns "temporarily unavailable" — **never a guess dressed up as an answer.**

## 10.10 Timeout with unknown transaction outcome

If a write's outcome is unknown after a timeout, the platform never assumes success and never
blindly retries. It queries the bank's system for actual status using the same idempotency key
before doing anything else; if status genuinely cannot be determined, the human sees "status
unknown, verify with your bank," not a guess in either direction.

## 10.11 MCP server compromise containment

MCP server instances are deployed per tenant (§2.4). Every tool response is schema-validated
before being trusted (§8.4).

---

# 11. Conversation History & Memory

Conversation history is a **first-class protected data domain**, distinct from documents or
account records, with its own leakage risks.

## 11.1 Ownership hierarchy

```
Conversation
├── tenant_id
├── user_id
├── session_id
├── conversation_id
├── module_id
├── access_scope
├── created_at
└── retention_policy
```
Bank → User → Session → Conversation — two users with the same role never share access to each
other's conversations by default.

## 11.2 Context Builder

A named component sits between authorization and the LLM, and **cannot freely fetch history**. It
receives tenant, user, session, conversation, and scope as constraints and returns only content
satisfying all of them — the same discipline as CoVe's independence being enforced by what a
function signature is even allowed to accept.

## 11.3 Authorization applies even to prior conversation content

If a permission changes between turns, old conversation content does not grant continued access —
authorization is re-checked at the moment of use, never inherited from the past (§6.6 restated for
this domain).

## 11.4 Privileged access to another user's conversation

Requires an explicit privileged capability (conversation owner, authorized supervisor, compliance
investigator, security administrator, audit administrator) governed by policy, and **privileged
access is itself audited** — even an administrator does not get unrestricted default access to
everyone's conversations.

## 11.5 Cache correctness for conversational data

Cache keys include tenant, user (or the exact resolved access scope), document/corpus version, and
query — never role or tenant alone. **Highly sensitive transactional queries are not cached at
all**, since some things are cheaper to recompute than to risk a key collision on.

## 11.6 Memory defaults

Same-session context is on (it's how a conversation works). Cross-session memory, personalized
memory, and conversation embeddings are **off by default**, bank-configurable, never assumed.

## 11.7 If long-term memory is ever built

It goes through the exact same pipeline as document ingestion: PII detection/redaction →
authorization classification → memory eligibility policy → embedding → **user-scoped memory
index**. Never one shared vector index for all users with authorization hoped-for after the fact —
same defense-in-depth principle as tenant isolation (§4.4), applied here.

## 11.8 Derived artifacts are the same protected information

Summaries, embeddings, extracted memory, and metadata are derived representations of the same
protected content. **Deleting a conversation cascades to all of them** — a summary can leak
exactly what redaction was supposed to prevent.

## 11.9 Session hygiene, particularly for branch environments

Idle-session auto-lock, full state clearing on logout, no lingering session in browser storage —
shared terminals are normal in a bank branch, not an edge case. Re-authentication is required for
sensitive operations; sessions and devices are tracked.

## 11.10 Adversarial test suite for this domain (feeds §16)

User A retrieving User B's conversation; Bank A retrieving Bank B's conversation; an employee
accessing another employee's HR conversation; a manipulated `conversation_id` (IDOR); a cache
collision across users; a deleted conversation surviving through its old embedding; unauthorized
access to an escalated conversation; cross-session context bleeding; prompt injection attempting
to surface hidden history. **Expected result for every case: zero disclosure.**

---

# 12. Data Layer

## 12.1 Path A and Path B, both genuinely exercised

Unlike the research corpus (100% digital-native PDF), a bank has substantial structured data (CBS
exports, transaction logs, HR records, CRM tables) and scanned documents (paper KYC forms, older
contracts). Both Path A (unstructured, LLM segmentation) and Path B (structured, zero LLM calls,
templated) and the OCR tier are **genuinely exercised and tested**, not merely unit-tested and
unexercised as in the research phase — using synthesized realistic fixtures where no real bank
data is available (Principle 8).

## 12.2 Document classification and versioning

Every document gets an access classification at ingestion — Public, Internal, Confidential,
Restricted, Customer-Sensitive, Regulatory, Executive, HR, Compliance/Security-Sensitive — feeding
RBAC/ABAC filtering before retrieval, not inferred from content at query time. Documents carry
version history; **current and superseded versions are never blended into one answer** — a
compliance query about "the policy in 2023" retrieves the version effective at that date, not the
current one, and vice versa for ordinary queries.

## 12.3 Live vs. indexed data

Two distinct knowledge paths (already stated in §10.8): static knowledge goes through offline
ingestion; live/transactional data is fetched fresh at answer time and never indexed or cached.

## 12.4 Ingestion hardening

Malware/virus scanning on every uploaded file before it reaches the parsing pipeline; macro-bearing
Office documents rejected or stripped, never executed; a configurable hard file-size ceiling;
duplicate detection by content hash (not filename), so a re-upload dedupes or versions rather than
double-indexing.

## 12.5 Data retention, systematized per data class

Every data class (documents, propositions, conversations, audit logs, PII vault entries, cache
entries) carries an explicit, tenant-configurable retention period, defaulting conservatively until
the bank sets its own. Deletion cascades to every derived artifact (embeddings, summaries, cache) —
the rule already established for conversations (§11.8), generalized to every data class.

## 12.6 Legal holds

A hold flag on any specific record (a conversation, a customer record, a document) overrides
normal retention/deletion schedules. Deletion requests and retention expiry both check for an
active hold first and refuse to proceed if one exists; setting/lifting a hold is itself an audited
event.

---

# 13. Confidence & Numeric Governance

## 13.1 Composite confidence

Formal field on every answer record, composed from: retrieval/rerank scores, numeric- and
period-verbatim guard pass/fail, CoVe match rate, extraction tier, OCR confidence, calculation
verification result. Not an implied signal — an explicit number the Governance layer and
human-escalation threshold both key off.

## 13.2 Two-level thresholds

A **platform-enforced minimum floor** for dangerous categories (credit, AML, fraud) that a bank
can tighten but never weaken, plus a **bank-configurable policy** on top, per module/category.
Any specific numeric thresholds mentioned anywhere in this document or its supporting conversation
are **illustrative starting defaults**, not prescribed production values — each bank sets these
through its own risk validation.

## 13.3 Calibration

A standing part of the evaluation harness (§22.3): bucket historical answers by stated confidence,
measure actual accuracy per bucket, plot the calibration curve. A systematically overconfident
bucket is a required fix before that confidence range can be trusted for escalation decisions.

## 13.4 Numeric safety (inherited and reaffirmed)

LLM never performs authoritative arithmetic; formulas are version-controlled (§2.7); inputs trace
to verified propositions; ambiguous formulas cause abstention; units, currency, and periods are
preserved and canonicalized before comparison; rounding is deterministic; derived values can be
independently recomputed for verification.

---

# 14. Auditability

## 14.1 What is captured

Every retrieval, tool call, LLM call, permission check, escalation decision, configuration change,
and authentication event — tagged against tenant, user, and session.

## 14.2 Tamper-resistance mechanism

**Append-only, hash-chained log** — each record embeds a hash of the previous record, so altering
history breaks the chain visibly. This works identically across SaaS, dedicated, and on-prem
deployments without requiring a separate exotic storage product.

## 14.3 Access control on audit records

**No role — including platform super-admin — has a delete capability on audit records.** Only
retention/legal-hold rules (§12.5, §12.6) govern eventual expiry. Read access to audit records is
itself permission-scoped, and audit records are themselves subject to the same PII controls as any
other data domain (Principle 4).

---

# 15. Observability

## 15.1 Langfuse integration

Aggregation layer over Agents SDK trace spans (§2.9). **Redaction happens before export to
Langfuse, not after** — the same pipeline guarding the LLM's input guards what gets traced. Access
to view traces is tenant-scoped through the same identity/authorization system as everything else,
never a separately unguarded observability tool.

## 15.2 Platform-wide monitoring

Availability, latency, token consumption, cost, error rates, tool failures, authentication and
authorization failures, prompt-injection detections, anomalous behavior, cross-tenant access
attempts, connector failures, agent-loop behavior, confidence degradation — aggregated across
tenants in a **privacy-safe** way (no tenant content, only metrics), distinct from any one tenant's
own observability view.

---

# 16. Evaluation Strategy

## 16.1 Three-arm comparison, generalized beyond document Q&A

LLM-only (no retrieval), naive RAG (the existing C0 baseline), and the Extended System (C4+ —
NPCE, CoVe, and the Query Router's expansion/decomposition logic, run as a single integrated
configuration rather than the preliminary study's five-cell C0–C4 ablation) — extended to
whichever module is being evaluated, not only Document Intelligence.

## 16.2 RAGAS as a secondary, comparability layer

Deterministic exact-match against a verified gold key remains the **primary** faithfulness measure
(the reasoning that motivated omitting RAGAS in the original research build still holds: its own
scoring is LLM-judged). RAGAS runs alongside it so results are legible against standard RAG
literature.

## 16.3 Trust-and-safety evaluation (new category, necessary at platform scale)

Adversarial tests proving the guardrails hold, not just that answers are accurate:

- Cross-tenant retrieval attempts (assert zero results)
- IDOR-style attempts to reach another customer's or employee's record
- Prompt-injection red-team tests against user queries, document content, and tool/connector
  responses
- OTP/auth-flow brute-force attempts
- Escalation fail-closed timeout verification
- The full conversation-isolation adversarial suite (§11.10)
- The full "can the AI do this" structural-enforcement check (§25, Part 2)

## 16.4 Confidence calibration (§13.3)

## 16.5 Agentic-mode metrics

Task completion, trajectory correctness, tool-call count as a cost proxy — distinct from
single-turn answer accuracy, since Agentic-mode work is not single-shot Q&A.

## 16.6 Cost and latency harness

Tracked per module and per tier, feeding back into the budget/routing decisions of §2.8.

## 16.7 Per-module golden datasets

Only Document Intelligence has an existing benchmark (FinanceBench-Collision) to build on. Every
other module needs a **synthesized golden dataset**, same testing discipline (Principle 8), built
before that module ships.

## 16.8 One shared, parameterized harness

Generalizes the existing per-cell gate scripts (`evaluation/run_c{0..4}_gate.py`) to run against
any module, tenant, or configuration combination through the same swappable interfaces (§3), not a
hardcoded new script per module.

---

# 17. Administration

## 17.1 Bank Admin Portal (tenant-facing)

One unified portal (not scattered screens) with these sections:

- **Identity & Customer Authentication** — identity provider selection, customer authentication
  flow builder, RBAC role assignment, ABAC/PBAC configuration
- **Core Banking / Connectors** — the onboarding wizard (§10.7) for CBS and every integration
- **Security & Governance** — PII redaction categories, confidence thresholds (bounded by
  platform floor), escalation rules, human-approval toggles, encryption/BYOK settings
- **Users & Roles** — inviting staff, assigning attributes, mapping IAM groups to platform roles
- **Data** — document upload/classification, retention policy per data class, legal-hold flags,
  ingestion status
- **Audit & Entitlement** — viewing audit logs (subject to internal role restriction), current
  plan, enabled modules, renewal status
- **Custom Requests** — submitting a request for a bespoke connector or proprietary authentication
  flow, which routes into the Platform Admin Console for security review (never self-approved by
  the bank)
- **Testing** — Test Connection, Test Authentication, Test Encryption, Test API, Run Contract
  Tests

**Internal least-privilege applies to this portal too.** No single flat "Bank Admin" role can
touch everything — connector management, confidence/escalation policy, and role assignment are
independently permissioned within the bank's own team, and the most sensitive changes require a
second admin's confirmation (same "no super-admin" rule as the platform side, §17.2).

**Admin bootstrap** is defined in §6.3.

## 17.2 Platform Admin Console (our internal operator surface — distinct from §17.1)

- **Tenant lifecycle** — create/suspend/offboard tenants, view health, assign plans, generate/
  revoke signed entitlements
- **Plan Registry** — add, rename, reprice, or disable pricing tiers and modules at any time,
  independent of a code release (§24.3)
- **Break-glass access console** — requesting elevated access to a tenant's data, routed through
  tenant consent, time-bound, fully audited both internally and in the tenant's own portal
- **Model & prompt governance** — model registry, prompt version history, the call-site registry
  (§2.6)
- **Change & release management** — canary controls, feature flags per tenant, rollback triggers,
  gate-suite results per release (§18)
- **Security operations dashboard** — dependency/SBOM status, scan results, pen-test findings,
  incident tracking (§8.9)
- **Cross-tenant observability** — aggregated, privacy-safe platform health and anomaly signals
  (§15.2)
- **Support/ops console** — redacted-by-default ticket/session lookup, explicit escalation path
  into break-glass when genuinely required
- **Connector/MCP administration** — managing the approved plugin registries (§10.2), reviewing a
  bank's bespoke connector request
- **Licensing & billing operations** — entitlements issued, renewals, usage reporting split
  cleanly between our charges and third-party consumption
- **Compliance & audit tooling** — evidence packages per tenant/jurisdiction, legal-hold management
  across tenants
- **Platform-wide safety floors** — confidence minimums, approved formula registry, approved
  connector-plugin registry, global rate-limit defaults

**This console has its own RBAC and least-privilege discipline, applied to our own staff.** A
support engineer does not have break-glass approval authority; someone managing the Plan Registry
does not need access to the security operations dashboard. Same object-level, least-privilege
thinking as everywhere else in this document, turned inward on the vendor's own organization.

---

# 18. Model & Prompt Governance, Change Management

## 18.1 Model governance

Every model requires **explicit per-tenant approval** before production use. Exact versions are
pinned, never silently auto-upgraded — a provider swapping model weights under the same name is
treated as a version change requiring re-approval and re-evaluation against the gate suite. A
model registry tracks provider, version, approved modules, and retirement status.

## 18.2 Prompt governance

Prompts are version-controlled like code. Tenant customization is limited to an approved extension
point (institution-specific terminology) — never free rewriting of the system prompt; a tenant
cannot weaken the numeric guard or the identity-never-claimed rule, since those are structural
guarantees, not prompt text. Every production response records the exact prompt, model, and
config version that produced it, for reproducibility.

## 18.3 Change management

Any change to model, prompt, retriever, reranker, embedding model, guardrail, connector, or
authorization policy requires: versioning, a mandatory pass through the evaluation gate suite
(§16), canary rollout to a small slice of traffic or a single pilot tenant, and a defined rollback
path. Nothing reaches every tenant simultaneously.

## 18.4 Configuration versioning/rollback

Every configuration change creates a new immutable version rather than overwriting the previous
one. The previous version stays available for one-click rollback. A new version must pass "test
connection" before it can be promoted to active.

---

# 19. Disaster Recovery & Business Continuity

## 19.1 Default targets (configurable per tenant/deployment)

RPO (max acceptable data loss window): 15 minutes, via continuous replication or frequent backups.
RTO (max time to full platform restoration): 4 hours.

## 19.2 External dependency failures

LLM provider, CBS, bank middleware, and IAM failures are handled by **graceful degradation to
abstention/unavailable messaging**, not treated as a platform disaster state — these are not our
infrastructure, and the response is "temporarily unavailable," never a guess (§10.9 restated).

## 19.3 Tenant-independent restore

Backups are tenant-scoped. One tenant's restore process must never touch or affect another
tenant's data.

---

# 20. Regulatory & Compliance

## 20.1 Nigeria-specific mapping (initial target market)

| Regulatory concern | Satisfied by |
|---|---|
| NDPA (data protection) | The Privacy layer (§7) |
| CBN cybersecurity / outsourcing guidance | On-prem/dedicated deployment options (§4.5), the security operations program (§8.9) |
| AML/KYC obligations | The Compliance/AML module's evidence-and-citation requirements (inherited faithfulness core) |
| Banking secrecy / confidentiality | Tenant and customer isolation (§4.4, §6.6) |

## 20.2 Honest caveat

This architecture is designed to be **compliance-shaped**. It is not self-certifying. Final
regulatory sign-off requires review by each bank's own legal/compliance counsel before go-live,
not a claim made by this document alone.

## 20.3 Jurisdictional adaptability

Compliance policies (retention periods, PII categories, data residency requirements) are
tenant-configurable, so the same architecture adapts to a different jurisdiction's requirements
through configuration, not a redesign.

---

# 21. Testing Discipline

Every functionality is tested as it is built — no untested code ships (Principle 8). Where no
realistic data exists (a bank's CBS responses, a scanned KYC form, a multi-year policy version
history, varied connector auth/encryption combinations), realistic synthetic fixtures are built
deliberately, proving the functionality rather than assuming it. This applies with equal rigor to
security/adversarial tests (§16.3) as to functional correctness tests.

---

# 22. Licensing & Commercial Model

## 22.1 Three-part billing separation

1. **Platform License** — the software itself: RAG core, NPCE, CoVe, governance, security,
   connector framework, enabled modules. **This is the only thing the platform charges for.**
2. **Deployment** — SaaS, dedicated cloud, or on-prem. Changes the license price, not who pays for
   inference.
3. **AI & third-party consumption** — LLM calls, SMS/OTP, any external API. **Always the bank's
   own account, own credentials, own cost. The platform never marks up or resells model
   inference, and never pays a bank's inference bill on their behalf.**

## 22.2 Signed entitlement mechanism

Each tenant's actual contract is a **cryptographically signed license document** — plan,
deployment type, seat/capacity band, per-module enable flags, per-governance-feature enable flags,
support tier, expiry — signed with a private key the platform vendor holds, verified locally by a
public key baked into the platform (works identically for an air-gapped on-prem deployment, since
verification needs no external call). A bank can read its entitlement; it cannot forge a better
one without the private key.

**No hard ceiling on Enterprise, by design** — a large, complex bank's actual price is base license
plus deployment modifier plus capacity band plus any custom entitlements, not a flat number.

## 22.3 Plan Registry — fully configurable, per explicit instruction

Tier names, prices, included modules, seat/capacity policy, deployment options, and support levels
all live in an **editable registry**, not hardcoded in the platform. The vendor can add, rename,
reprice, or retire any plan element at any time with no engineering release required. A standard
plan is a pre-built entitlement template; any customer — especially Enterprise — can receive a
fully custom entitlement bundle that matches no named plan at all.

## 22.4 Current pricing table (base defaults in the Plan Registry, editable at any time)

| | Starter | Professional | Business ★ | Enterprise |
|---|---|---|---|---|
| Base license (SaaS, /year) | ₦5,000,000 | ₦15,000,000 | ₦35,000,000 | ₦75,000,000+ (no ceiling) |
| Seats included | Unlimited | Unlimited | Unlimited | Unlimited |
| Modules | Customer + Employee AI, Basic Operations | + Full Employee Experience, Banking Operations | + Full Banking Operations, ABAC/PBAC | Full suite + custom entitlements |
| Governance | Basic | Advanced | Full ★ | Enterprise ★ + custom policies |
| Deployment eligible | SaaS only | SaaS only | SaaS or Dedicated | SaaS, Dedicated, or On-Prem |

**Explicit decisions locked into this table:**
- **Seats default to Unlimited across all four tiers**, editable per-tier at any time. With seats
  unlimited everywhere, tiers currently differentiate purely on modules, governance depth, and
  deployment options — this is a deliberate starting position, not an oversight.
- **Customer-facing usage (retail customers using the Customer Experience pillar) is explicitly
  outside the pricing model entirely** — not metered, not capped, not a future pricing dimension.
  It is the bank's own customer base, not a platform seat.

## 22.5 Deployment modifier (applies on top of base license, values editable in the Plan Registry)

SaaS — included. Dedicated Cloud — configurable uplift percentage. On-Premises — configurable
uplift percentage, or a custom quote for large/complex deployments.

## 22.6 Support & SLA — independent add-on dimension

Standard, business-hours, 24/7, dedicated technical account manager, security-incident response —
any customer can add any level, priced and toggled independently of which plan they are on.

## 22.7 Capacity band

For Business/Enterprise custom deals only, not a public metered menu: customer volume, document
volume, transaction throughput, connector count — one configurable field per entitlement, keeping
the published table simple while letting a high-volume bank's actual contract reflect its real
scale.

## 22.8 Implementation & Professional Services (one-time, separate from the annual license)

IAM integration, CBS/connector setup, data ingestion, security review — scoped and quoted per
engagement. Custom connector builds priced separately, one-time or recurring as negotiated.

## 22.9 License enforcement by deployment type

- **SaaS and dedicated-cloud** — fully enforceable; the vendor controls the runtime entirely, so no
  meaningful workaround exists.
- **On-premises — the honest exception.** No purely technical protection is absolute once software
  runs on hardware the bank fully controls (true of every on-prem enterprise vendor, not unique to
  this platform). The actual posture is a combination: the signed license (§22.2); a periodic
  license-validation heartbeat for on-prem deployments that permit outbound connectivity (only a
  license ping leaves, never customer data); an offline signed-file renewal process for fully
  air-gapped deployments; and — the part that deters *deliberate* circumvention rather than merely
  raising the technical bar — **contractual audit rights and penalties**, which carry unusual
  weight with banks specifically, given their existing regulatory exposure.

## 22.10 On-prem, fully defined

- **On-prem means the entire platform** — application layer, data layer, connector framework —
  **deploys inside the bank's own infrastructure.** It is not the vendor collecting connection
  details while the platform remains hosted elsewhere.
- **Four-way responsibility boundary:**
  - *Our software* — signed, versioned artifacts the vendor controls and supports.
  - *Bank configuration* — endpoints, credentials, policies, IAM mappings.
  - *Bank infrastructure* — their servers, network, storage, keys, firewalls.
  - *Custom connector code* — jointly governed through the connector framework plus the commercial
    agreement, since this is the one place code is genuinely written for a specific bank.
  - **A bank that modifies the vendor's application code directly falls outside the vendor's
    support obligation.**
- **Deployment package deliverable:** container images, deployment charts (e.g. Helm),
  configuration schemas, connector packages, migration scripts, the signed license, deployment
  documentation. The bank's infrastructure team deploys it inside their approved environment, with
  vendor implementation assistance.
- **"Dedicated" does not necessarily mean the vendor's cloud account** — it can run in the bank's
  own cloud account, isolated and dedicated rather than shared multi-tenant. The distinguishing
  factor from SaaS is isolation, not whose cloud bill it is.

## 22.11 Billing

Billable: platform license, deployment, implementation, professional services, custom connectors,
optional governance add-ons, support. **Explicitly never billed by the platform:** LLM/API
inference, SMS/OTP delivery, any third-party consumption — stated as a commercial principle in
every contract, not left as an inferred implementation detail. Usage reporting distinguishes
platform charges from third-party consumption clearly enough that a bank never has cause to ask why
they're being charged twice for tokens.

---

# 23. Architecture-Completeness Review — Findings & Resolutions

A formal pre-implementation review (56 sections) was conducted against this plan before this
document was finalized. Its purpose was to discover what had not yet been thought about, not to
confirm the plan sounded complete. The following captures its findings and every resulting
decision, in full.

## 23.1 Fully covered at review time

Multi-tenancy and defense-in-depth isolation · customer/employee object-level authorization · IAM
federation and token lifecycle · customer authentication flow engine · PII reversible tokenization
· retrieval-time permission filtering · document-content prompt injection defense · BYO-LLM tenant
scoping · agentic/controlled mode gating · connector framework · bank middleware indirection ·
transaction-intent/execution separation and idempotency · human escalation triggers and fail-closed
timeout · confidence floors · calculation guard and formula registry · conversation isolation and
Context Builder · cache security · licensing/billing separation and Plan Registry · deployment
model definitions.

## 23.2 "Can the AI do this?" — structural enforcement check

| Capability | Structurally possible? | Enforcement point |
|---|---|---|
| Read another bank's data | No | DB-level RLS + vector namespace isolation |
| Read another customer's data | No | Object-level check against session-resolved account list |
| Read another employee's data | No | Object-level check + ABAC attributes |
| Change its own permissions | No | No write path from AI output to permission tables exists |
| Create permissions | No | No such tool exists in the design |
| Access secrets | No | Secrets never enter LLM context |
| Reveal credentials | No | Redaction before logging/tracing/prompting |
| Reveal hidden prompts | Not guaranteed impossible, but harmless if it happens | Nothing dangerous is ever placed in the prompt (§8.5) |
| Execute arbitrary SQL/GraphQL/shell | No | No generic execution tool exists |
| Call arbitrary URLs/APIs | No | Closed connector registry, no free-form HTTP tool |
| Transfer arbitrary money | No | AI produces intent only; bank's system executes |
| Modify arbitrary customer records | No | Writes require allowlist + object-level check |
| Bypass human approval | No | Fail-closed timeout, no auto-approval path |
| Approve its own escalation | No | Requester-id/reviewer-id check (§9.4) |
| Override confidence thresholds | No | Platform floor is non-negotiable |
| Switch Controlled → Agentic | No | Mode pinned per module, never runtime-inferred |
| Choose another tenant/customer | No | Identity resolved once from session, never from content |
| Impersonate another user | No | Same as above |

## 23.3 All items requiring a decision — resolved in full

**Reviewer self-approval.** Resolved in §9.4.

**Audit tamper-resistance.** Resolved in §14.2 — append-only, hash-chained log.

**Platform-operator access boundary.** A distinct "Platform Operator" role has **zero default
access** to any tenant's business data. Anything beyond that is break-glass only: tenant consent
required, time-bound, fully audited, visible in the tenant's own admin portal (§17.2).

**Compromised MCP server blast radius.** Resolved in §2.4, §10.11 — per-tenant MCP instances,
schema-validated tool responses.

**Agentic hard limits.** Concrete configurable defaults: 10 tool calls max per task, 5-minute
wall-clock ceiling, a configurable cost ceiling, 3 levels of recursion, 2 retries per tool.
Breaching any limit stops execution and returns an explicit "incomplete — limit reached" result —
never a silent continuation or fabricated completion. Full trajectory logged and replayable for
audit.

**Document ingestion hardening.** Resolved in §12.4.

**Data classification taxonomy.** Resolved in §12.2 — fixed enumeration of nine levels.

**Regulatory mapping.** Resolved in §20.

**Disaster recovery / RPO-RTO.** Resolved in §19.

**Security operations program.** Resolved in §8.9.

**Model/prompt governance.** Resolved in §18.1, §18.2.

**Change management.** Resolved in §18.3.

**Frontend web security.** Resolved in §8.7.

**Supply-chain security.** Resolved in §8.10.

**Tenant offboarding.** Immediate access disablement; full credential/token/key revocation; audit
records exported to the bank per their retention obligations; tenant data deleted within a
contracted window; tenant-specific encryption keys **crypto-shredded**, not merely row-deleted;
connectors disabled; license revoked. Every step logged and independently confirmable.

**Support/ops safe debugging.** Resolved in §17.2 — redacted-by-default tooling, break-glass for
full access, scoped to the specific ticket under investigation.

**Provider training/retention guarantee.** Required as a mandatory onboarding step for any tenant
using an external LLM provider — zero-retention/no-training terms verified against that provider's
enterprise agreement and recorded per tenant, never assumed by default.

**Timeout-unknown-transaction-outcome.** Resolved in §10.10.

**Confidence calibration method.** Resolved in §13.3.

**Formula coverage.** Resolved in §2.7 — a defined, finite starter set; anything outside it causes
abstention by design.

**Configuration versioning/rollback.** Resolved in §18.4.

**Admin portal privilege separation.** Resolved in §17.1, §17.2 — no single super-admin on either
side, two-person authorization for the most sensitive changes.

**Observability/trace redaction.** Resolved in §15.1.

**Encryption key rotation ownership.** Resolved in §8.2.

**Resource isolation / noisy-neighbor.** Resolved in §5.4.

**Tenant onboarding, full sequence.** Contract signed → tenant created in the Plan Registry →
signed entitlement generated → bank admin invited (via bootstrap credential, §6.3) → identity
provider connected → LLM provider/key configured → connectors configured and tested through the
onboarding wizard → data ingested and validated → RBAC/ABAC/PBAC configured → confidence and
escalation policy set → security review completed → contract test suite run against real or
staging connections → production activation. Most steps are self-service through the Bank Admin
Portal; security review and first production activation require vendor sign-off.

**Legal holds.** Resolved in §12.6.

**Data retention systematized per data class.** Resolved in §12.5.

**Hidden-prompt exfiltration resistance.** Resolved in §8.5.

## 23.4 Critical / security / production blockers identified

**Critical blockers** (must resolve before implementation — all now resolved above): platform-
operator access boundary, audit tamper-resistance mechanism, compromised-MCP blast radius/response
validation, agentic hard limits, document ingestion hardening, reviewer self-approval prevention.

**Security blockers** (could directly cause the exact failure modes this entire plan exists to
prevent — all now resolved above): compromised-MCP-server exfiltration path, hidden-prompt
exfiltration posture, reviewer self-approval, frontend web vulnerabilities, supply-chain compromise
blast radius, disabled-IAM-account-mid-session session survival (resolved in §6.9).

**Production blockers** (unsafe to deploy to a real bank without these — all now resolved above):
disaster recovery/RPO-RTO, security operations program, regulatory mapping, tenant offboarding,
data classification taxonomy, model/prompt governance.

## 23.5 Explicitly deferred to post-MVP

The full module-by-module review (19 modules × 15 structured questions each) is **not** attempted
in this document — it is too large to front-load meaningfully and should be built systematically
as each module is actually implemented, using the template in §23.6, not all at once speculatively.
Noisy-neighbor resource isolation matters at scale, not at first customer, and can follow shortly
after initial launch. Agentic trajectory replay tooling is needed once Agentic-mode modules ship,
not before. Configuration-rollback UI polish can lag the underlying versioning mechanism (§18.4),
which is needed sooner.

## 23.6 Module-by-module review template (to be completed per module as it is built)

For each module: Controlled or Agentic? What data does it access? What tools does it access? What
writes can it perform? What roles can access it? What object-level restrictions exist? What
confidence threshold applies? What causes escalation? What evidence is required? What data can
leave the tenant? What model can it use? What are its failure modes? What are its evaluation
metrics? What regulatory risks exist? What additional guardrails are required?

## 23.7 What a competent team would still need to invent

Named honestly rather than glossed over: the exact numeric bounds for agentic execution (the
defaults in §23.3 need empirical tuning against real usage, not just a stated principle); the
precise audit-log storage implementation (hash-chaining is specified, the exact storage backend is
an implementation choice); the disaster-recovery RPO/RTO targets are business decisions requiring
sign-off, not something this document can dictate in the abstract; and the module-by-module table
(§23.6) requires filling in as each module is actually scoped.

---

# 24. Relationship to the Existing Research Codebase

The `financial_rag` repository's Path A/B offline pipeline, hybrid retrieval core, NPCE query
splitter, CoVe orchestrator, numeric/period guards, citation binder, and abstention gate become —
with the multi-tenant schema amendment noted in §4.2 and the connector/authentication layers wired
around them — the implementation of the **Document Intelligence** module inside **Banking
Operations**, and the working reference implementation of the platform's **AI Governance** layer's
hallucination-detection, citation-verification, and confidence-scoring components. Its acceptance
tests (`tests/test_acceptance.py`) and its FinanceBench-Collision evaluation results
(`Findings_and_Conclusion.md`) remain valid evidence for that module specifically and do not need
to be re-derived — they need to be extended with the multi-tenant, security, and governance layers
described in this document.

---

# 25. Concept Glossary

Kept deliberately separate because these concepts blur together easily and the blurring is where
real security gaps hide:

- **IAM (Authentication)** — "What identity does the bank say this person has?"
- **RBAC** — "What can this role normally do?"
- **ABAC/PBAC** — "Can this particular person perform this action on this particular resource
  under these conditions?"
- **Privacy (PII protection)** — "What sensitive information should the AI be allowed to see?"
- **RAG Governance (NPCE/CoVe/citation)** — "Is the answer sufficiently supported and trustworthy?"
- **Confidence** — "How strong is the evidence?"
- **Human-in-the-loop** — "Does a human need to approve or review this?"
- **MCP / Connector Framework** — "How does the AI platform safely interact with the bank's
  external systems?"

---

# 26. Deliberately Deferred

Listed explicitly so nothing here is mistaken for an oversight:

- The full module-by-module review (§23.6) — built per module as each is scoped, not all upfront.
- Noisy-neighbor resource isolation tuning — follows initial launch, not a launch blocker.
- Agentic trajectory replay tooling — needed once an Agentic-mode module actually ships.
- Configuration-rollback UI — the underlying versioning mechanism is required sooner than its UI.
- Web-search retrieval and Program-of-Thoughts extensions beyond the calculation module defined in
  §2.7 — reserved via the `external` provenance tier, not built in this phase.
- Jurisdictions beyond Nigeria's initial regulatory mapping (§20) — the mechanism is
  jurisdiction-agnostic; specific mappings for other markets are built when a bank in that market
  is onboarded.

---

*End of master specification. Phasing — the build order that turns this document into a sequence
of shippable milestones — is defined in a separate, subsequent document.*
