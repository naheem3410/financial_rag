# Master Technical Architecture and Implementation Plan
## Part 1 — Master System Architecture

**Relationship to prior documents.** This part translates
`ENTERPRISE_PLATFORM_ARCHITECTURE.md` into concrete, buildable components. It introduces no new
product capability — every component below exists to implement a decision already made in that
document. Where a component's behavior is fully specified there, this part references it rather
than restating it, and focuses on responsibility, data ownership, interfaces, and trust
boundaries.

**Notation used throughout.** For every component: **Responsibility**, **Data owned**,
**Inputs**, **Outputs**, **APIs**, **Dependencies**, **Trust boundary**, **Tenant boundary**,
**Authentication**, **Authorization**, **Failure behavior**.

---

## 1. System Map

```
                                    ┌─────────────────────────────┐
                                    │        Web Applications      │
                                    │  (Customer / Employee / Bank │
                                    │   Admin / Platform Admin)    │
                                    └───────────────┬───────────────┘
                                                    │
                                    ┌───────────────▼───────────────┐
                                    │          API Gateway          │
                                    └───────────────┬───────────────┘
                                                    │
          ┌─────────────────────────────────────────┼─────────────────────────────────────────┐
          │                                         │                                         │
┌─────────▼─────────┐                    ┌──────────▼──────────┐                    ┌──────────▼──────────┐
│ Identity / IAM     │                    │  Tenant Service      │                    │ Configuration Service│
│ Authentication Svc │                    │  User/Session Svc    │                    │ Policy Engine         │
│ Authorization Engine│                   └──────────┬──────────┘                    │ Plan Registry         │
└─────────┬──────────┘                               │                                └──────────┬──────────┘
          │                                          │                                           │
          └───────────────────┬──────────────────────┴───────────────────────┬──────────────────┘
                              │                                              │
                  ┌───────────▼───────────┐                       ┌──────────▼──────────┐
                  │ Conversation Service   │                       │   Audit Service       │
                  │ Context Builder        │                       │ (hash-chained)        │
                  └───────────┬───────────┘                       └──────────┬──────────┘
                              │                                              │
                  ┌───────────▼─────────────────────────────────────────────▼──────────┐
                  │                     RAG Orchestration                                │
                  │  Query Processing → Retrieval → Reranking → Evidence/Proposition     │
                  │  Layer → Guardrails → Confidence Engine → Calculation Engine         │
                  └───────────┬─────────────────────────────────────────────┬──────────┘
                              │                                              │
                  ┌───────────▼───────────┐                       ┌──────────▼──────────┐
                  │      LLM Gateway       │                       │  Agent Orchestrator   │
                  │ Model Registry         │                       │ (Agentic-mode modules │
                  │ Prompt Registry        │                       │  only, per §2.3)      │
                  └───────────┬───────────┘                       └──────────┬──────────┘
                              │                                              │
                              └──────────────────┬───────────────────────────┘
                                                 │
                                     ┌───────────▼───────────┐
                                     │  Tool / MCP Gateway    │
                                     └───────────┬───────────┘
                                                 │
                                     ┌───────────▼───────────┐
                                     │ Connector Framework    │
                                     └───────────┬───────────┘
                                                 │
                                     ┌───────────▼───────────┐
                                     │   Bank Middleware /    │
                                     │   CBS / External Sys   │
                                     └───────────────────────┘

  Cross-cutting, called from every layer above:
  PII/Tokenization Service · Secrets Management · Human Escalation · Observability ·
  Billing/Licensing · Backup/DR

  Offline plane, feeding the RAG Orchestration layer's indexes:
  Document Ingestion → Malware Scanning → OCR → Chunking/Proposition Extraction → Embedding →
  Indexing (Vector DB / Relational DB / Object Storage)

  Administrative plane, out-of-band from the request path above:
  Admin Portal (Bank-facing) · Support/Break-glass Tooling (Platform-facing)
```

---

## 2. Component Specifications

### 2.1 Web Applications

- **Responsibility.** Render the four distinct front-ends: Customer-facing app, Employee-facing
  app, Bank Admin Portal, Platform Admin Console. No business logic or authorization decision is
  ever made here — every check is re-verified server-side (Principle: frontend is never the
  authorization boundary).
- **Data owned.** None. Transient UI state only (browser storage per §11.9 of the architecture
  document — no sensitive conversation data persisted client-side).
- **Inputs.** User interaction, API responses.
- **Outputs.** API requests.
- **APIs.** Consumes the API Gateway's public and admin surfaces (Part 20 defines the foundational
  API contracts).
- **Dependencies.** API Gateway only. No direct dependency on any backend service.
- **Trust boundary.** Internet ↔ Platform (Part 3, boundary 1). Assumed fully untrusted;
  every request is re-authenticated and re-authorized at the Gateway and again at each service.
- **Tenant boundary.** The web app carries no tenant trust of its own — tenant context is
  resolved server-side from the authenticated session, never taken from a client-supplied field.
- **Authentication.** Delegates entirely to Authentication Service (§2.3) / Customer Authentication
  Flow Engine.
- **Authorization.** None performed here. CSRF tokens, CSP, frame-ancestors, and upload validation
  are implemented here as defense-in-depth (architecture §8.7) but do not substitute for
  server-side checks.
- **Failure behavior.** On any API failure, renders a generic error state — never surfaces stack
  traces, internal URLs, or backend error detail to the browser (architecture §45 error-handling
  principle).

### 2.2 API Gateway

- **Responsibility.** Single ingress point for all client traffic. Terminates TLS, applies
  platform-wide rate limiting and per-tenant/per-user resource isolation (architecture §5.4),
  performs initial authentication token validation, and routes to the correct backend service.
- **Data owned.** None persistent. Short-lived request/response logs (redacted before persistence,
  per architecture §15.1).
- **Inputs.** HTTPS requests from Web Applications and any direct API caller (architecture §8.8 —
  an API caller bypassing the UI remains exactly as constrained as one going through it).
- **Outputs.** Routed requests to backend services; aggregated responses to clients.
- **APIs.** Exposes the full public/admin API surface (Part 20). Internally routes via service
  discovery to Identity, Tenant, Conversation, RAG Orchestration, Agent Orchestrator, Admin, and
  Billing services.
- **Dependencies.** Authentication Service (token validation), rate-limiter (backed by Cache,
  §2.29).
- **Trust boundary.** Internet → Platform (boundary 1); Customer → Platform (boundary 2);
  Employee → Platform (boundary 3).
- **Tenant boundary.** Resolves tenant from the validated token/session, attaches it to every
  downstream request as immutable context — this is the single point where tenant resolution
  happens for a request; no downstream service re-derives it independently from client input.
- **Authentication.** Validates access tokens issued by the Authentication Service; rejects
  anything unsigned, expired, or malformed before it reaches any backend service.
- **Authorization.** Coarse-grained only (is this token valid, is this route publicly reachable at
  all) — fine-grained authorization is the Authorization Engine's responsibility (§2.5), applied
  downstream.
- **Failure behavior.** Fails closed — an unvalidatable token or an unreachable Authentication
  Service results in a rejected request, never a pass-through.

### 2.3 Identity / IAM Integration Layer

- **Responsibility.** Federates to a bank's own IAM (Azure AD/Entra ID, Okta, generic SAML/OIDC)
  or the platform-managed identity fallback (architecture §6.2). Normalizes identity claims into
  the canonical security context (Part 4).
- **Data owned.** IAM connection configuration per tenant (identity provider type, endpoint,
  claim-mapping rules). **Never** owns the bank's own user directory — that remains the bank's.
  For platform-managed identity tenants, owns hashed credentials, MFA secrets, lockout state.
- **Inputs.** Identity provider federation responses (SAML assertion, OIDC token); for
  platform-managed identity, login credentials.
- **Outputs.** A verified identity assertion (user ID, group memberships) to the Authentication
  Service.
- **APIs.** `POST /auth/federate/{provider}`, `POST /auth/login` (platform-managed only).
- **Dependencies.** Secrets Management (IdP client secrets/certificates), Tenant Service (which
  IdP configuration applies to this request).
- **Trust boundary.** Employee → Platform (boundary 3); also serves as one leg of Customer →
  Platform (boundary 2) when a bank uses OIDC/SAML for customers.
- **Tenant boundary.** IdP configuration is tenant-scoped and stored with the same database-level
  isolation as connector configuration (architecture §4.4).
- **Authentication.** This component *is* authentication for the employee/federated-customer path.
- **Authorization.** None — produces an identity, not a permission decision.
- **Failure behavior.** A federation failure or an IdP outage results in login failure with a
  generic message — never a fallback to an unauthenticated session.

### 2.4 Customer Authentication Flow Engine

- **Responsibility.** Executes the tenant-configured, state-machine-based customer authentication
  journey (architecture §6.3) from the closed primitive set (`CollectInput`, `VerifyIdentifier`,
  `Password`, `PIN`, `OTP`, `MFA`, `OAuth2`, `OIDC`, `SAML`, `TokenExchange`,
  `CertificateAuthentication`, `DeviceBinding`, `SessionCreation`, `TokenRefresh`,
  `Logout`/`Revoke`).
- **Data owned.** The flow definition (state graph) per tenant; transient flow state per
  in-progress authentication attempt (never a durable customer PII store — any identifier
  collected mid-flow, such as a BVN, is discarded once the flow completes or times out, not
  retained).
- **Inputs.** Customer-supplied flow-step data (BVN, OTP code, etc.), verification responses from
  bank endpoints via the Connector Framework.
- **Outputs.** On success, a verified customer identity handed to the Authentication Service;
  never handed directly to any AI/LLM-facing component (architecture §6.3 — the AI layer receives
  control only after the flow completes).
- **APIs.** `POST /auth/customer/flow/{tenant_id}/start`, `POST /auth/customer/flow/{flow_id}/step`.
- **Dependencies.** Connector Framework (for `VerifyIdentifier`/`OTP` steps that call bank
  endpoints), PII/Tokenization Service (any identifier logged is tokenized, never raw), Secrets
  Management.
- **Trust boundary.** Customer → Platform (boundary 2).
- **Tenant boundary.** Flow definitions are tenant-owned configuration.
- **Authentication.** This component *is* the customer authentication mechanism.
- **Authorization.** None.
- **Failure behavior.** Rate-limited per architecture §6.3 (tenant + customer identity + device +
  IP + session + endpoint); on repeated failure, lockout per tenant policy; on a step's downstream
  bank endpoint being unavailable, the flow reports failure honestly, never assumes success.

### 2.5 Authentication Service

- **Responsibility.** Issues, validates, refreshes, and revokes platform session tokens once an
  identity has been established by §2.3 or §2.4. Owns the Session/Token Manager described in
  architecture §6.4.
- **Data owned.** Active sessions, refresh tokens (encrypted at rest), device/session tracking
  records.
- **Inputs.** Verified identity assertions from §2.3/§2.4.
- **Outputs.** Signed session tokens (short-lived access token + refresh token) to the API Gateway
  / client.
- **APIs.** `POST /auth/token`, `POST /auth/token/refresh`, `POST /auth/logout`,
  `POST /auth/session/revoke` (admin-triggered, e.g. on IAM account disablement, §6.9).
- **Dependencies.** Secrets Management (token signing keys), Cache (session lookup).
- **Trust boundary.** Sits at the Application ↔ Database boundary internally (session store).
- **Tenant boundary.** Sessions are tenant-scoped; a token issued for one tenant is never valid
  against another.
- **Authentication.** N/A — this component performs authentication as its function.
- **Authorization.** None — produces a session, not a permission decision. **Never a token
  reaches the LLM/AI layer** (architecture §6.4) — only a resolved identity context object does.
- **Failure behavior.** An expired or revoked token is rejected, not silently renewed; on a
  detected IAM account disablement, all active sessions for that user are invalidated on next
  check (§6.9), not only at next login.

### 2.6 Authorization Engine

- **Responsibility.** Evaluates RBAC (mandatory baseline), optional ABAC/PBAC policy, and
  object-level authorization for every request that touches data or invokes a tool. This is the
  single component every other service calls for an allow/deny decision — no service implements
  its own ad hoc authorization logic.
- **Data owned.** Role definitions, ABAC/PBAC policy documents, role assignments (`user_roles`).
  Does not own the resource data being protected.
- **Inputs.** Security context (Part 4), requested resource/action, resource attributes
  (classification, owner, region, etc.).
- **Outputs.** Allow/deny decision, with reason (for audit).
- **APIs.** Internal-only (`authorize(context, resource, action) -> Decision`), called
  synchronously by every service in the request path — never bypassed.
- **Dependencies.** Tenant Service (role/policy configuration is tenant-scoped), Audit Service
  (every decision is logged, per architecture §6.6/§9.4/§11.3).
- **Trust boundary.** Internal to the platform; the one component whose decision every other
  boundary in Part 3 ultimately depends on.
- **Tenant boundary.** Policies are tenant-scoped and isolated at the database layer (architecture
  §4.4).
- **Authentication.** Assumes an already-authenticated security context; does not authenticate.
- **Authorization.** This component *is* authorization.
- **Failure behavior.** **Fails closed** — if the Authorization Engine is unreachable or returns
  an ambiguous result, the calling service denies the request. There is no "allow by default"
  path anywhere in the platform.

### 2.7 Tenant Service

- **Responsibility.** Owns the tenant record and tenant-level configuration: which deployment
  type, which plan/entitlement, which modules are enabled, which IdP is connected, which
  connectors exist.
- **Data owned.** `tenants`, tenant-level feature flags, tenant status (active/suspended/
  offboarding).
- **Inputs.** Tenant creation/update requests from the Platform Admin Console; entitlement
  documents from the Licensing Service.
- **Outputs.** Tenant configuration to every service that needs it (Authorization Engine,
  Connector Framework, Confidence Engine, etc.).
- **APIs.** `GET /tenants/{id}`, `POST /tenants` (Platform Admin only), `PATCH
  /tenants/{id}/status`.
- **Dependencies.** Licensing/Plan Registry (entitlement source of truth), Secrets Management.
- **Trust boundary.** Platform Operator → Tenant (boundary 11) — write access is Platform Admin
  Console only; read access is scoped so a tenant can only ever read its own record.
- **Tenant boundary.** This service *is* the tenant boundary's source of truth.
- **Authentication.** Delegates to Authentication Service.
- **Authorization.** Write operations require Platform Operator role; a Bank Admin may read/update
  only their own tenant's non-entitlement configuration.
- **Failure behavior.** If unreachable, dependent services fail closed (e.g., Authorization Engine
  cannot evaluate a policy without knowing the tenant, so it denies rather than guesses).

### 2.8 User / Session Service

- **Responsibility.** Owns user identity records within a tenant (employee and customer), distinct
  from the external IAM/CBS systems that are authoritative for the underlying person — this
  service is the platform's own mapping of "which platform user corresponds to which IAM/CBS
  identity," not a competing source of truth.
- **Data owned.** `users`, `user_roles`, device/session metadata (session content itself is owned
  by the Authentication Service, §2.5).
- **Inputs.** Identity claims from §2.3, role assignments from Bank Admin Portal.
- **Outputs.** User records to Authorization Engine, Conversation Service.
- **APIs.** `GET /users/{id}`, `POST /users/{id}/roles`.
- **Dependencies.** Tenant Service, Authorization Engine.
- **Trust boundary.** Employee → Platform (boundary 3); Customer → Platform (boundary 2).
- **Tenant boundary.** All user records are tenant-scoped with database-level isolation.
- **Authentication.** Delegates to Authentication Service.
- **Authorization.** Role assignment changes require the two-person authorization rule for
  sensitive changes (architecture §17.1) when granting elevated roles.
- **Failure behavior.** Fails closed on any lookup failure — an unresolvable user cannot proceed.

### 2.9 Conversation Service

- **Responsibility.** Owns the full conversation ownership hierarchy (architecture §11.1):
  tenant → user → session → conversation → message.
- **Data owned.** `conversations`, `messages`.
- **Inputs.** New messages from the RAG Orchestration / Agent Orchestrator pipeline.
- **Outputs.** Conversation history, scoped, to the Context Builder only.
- **APIs.** `POST /conversations`, `GET /conversations/{id}/messages`,
  `DELETE /conversations/{id}` (subject to retention/legal-hold check, architecture §12.6).
- **Dependencies.** Authorization Engine (object-level check on every read), PII/Tokenization
  Service (messages are redacted before storage per the same rule as ingestion).
- **Trust boundary.** Internal, but directly implements the User A → User B conversation
  isolation guarantee that Part 7 formalizes.
- **Tenant boundary.** Database-level isolation, same as all other tenant data.
- **Authentication.** Delegates to Authentication Service.
- **Authorization.** Every read of a conversation not owned by the requester requires an
  explicit privileged-access grant (architecture §11.4), itself audited.
- **Failure behavior.** On authorization ambiguity, denies access to the conversation rather than
  returning a partial or redacted view without an explicit reason logged.

### 2.10 Context Builder

- **Responsibility.** The single, constrained gateway between conversation history / retrieved
  evidence and the LLM. Implements architecture §11.2 exactly: accepts only
  `(tenant_id, user_id, session_id, conversation_id, module, authorization_scope)` and returns
  only content satisfying all of them. **Cannot be called with a broader query than this** — there
  is no method on this component that accepts an unscoped history request.
- **Data owned.** None — a pure, stateless assembly function over data owned by Conversation
  Service and the RAG Orchestration layer.
- **Inputs.** The scope tuple above.
- **Outputs.** An assembled, authorized context payload for the LLM Gateway.
- **APIs.** Internal-only: `build_context(scope) -> Context`.
- **Dependencies.** Conversation Service, Authorization Engine, RAG Orchestration (for
  retrieval-derived evidence).
- **Trust boundary.** LLM boundary (architecture Principle 4) — this is the enforcement point for
  "no data enters an LLM context merely because it exists in the platform."
- **Tenant boundary.** Inherits from the scope tuple; cannot cross tenants by construction.
- **Authentication.** N/A.
- **Authorization.** Re-validates scope against the Authorization Engine on every call — never
  trusts a cached prior authorization result for a new call.
- **Failure behavior.** On any authorization check failure, returns an empty/minimal context
  rather than a partial one — never silently drops the check and proceeds.

### 2.11 RAG Orchestration

- **Responsibility.** Coordinates Query Processing → Retrieval → Reranking → Evidence/Proposition
  assembly → Guardrails → Confidence scoring → Calculation (when applicable) for the Fixed
  Faithfulness Pipeline lane (architecture §2.1). Implements the Query Router (§2.5) and NPCE/CoVe
  behavior inherited from the research core.
- **Data owned.** None persistent — orchestrates calls to Retrieval, Reranking, and the Proposition
  layer.
- **Inputs.** A user query plus its security context, from the Context Builder.
- **Outputs.** A verified, cited answer plus a confidence score to the caller (API Gateway /
  Conversation Service).
- **APIs.** Internal: `answer(query, security_context, module) -> VerifiedAnswer`.
- **Dependencies.** Query Processing, Retrieval, Reranking, Proposition/Evidence Layer, LLM
  Gateway, Guardrails, Confidence Engine, Calculation Engine, Authorization Engine.
- **Trust boundary.** Application → LLM (boundary 8), internally.
- **Tenant boundary.** Every retrieval call carries tenant scope; enforced again at the Retrieval
  layer independently (defense in depth, architecture §4.4).
- **Authentication.** N/A — operates within an already-authenticated request.
- **Authorization.** Delegates object/classification-level filtering to Retrieval (§2.13); does
  not itself decide what's visible.
- **Failure behavior.** On any sub-component failure (retrieval timeout, LLM failure), returns
  abstention — "not found in the available filings" or equivalent — never a fabricated answer
  (architecture Principle 5, §45).

### 2.12 Query Processing (Analyzer + Splitter + Router)

- **Responsibility.** Noun-phrase collision detection, slot parsing (Call Site 3), and the
  deterministic Cartesian-product sub-query decomposition, plus the Query Router's
  direct-answer/rewrite/decompose/full-NPCE decision (architecture §2.5) — **only within the
  orchestration mode already assigned to the calling module** (§2.3 of the architecture document;
  this component never changes mode).
- **Data owned.** The metric/entity lexicon (tenant-extensible).
- **Inputs.** Raw query, module identity, security context.
- **Outputs.** One or more `SubQuery` objects.
- **APIs.** Internal: `transform(query, context) -> Sequence[SubQuery]`.
- **Dependencies.** LLM Gateway (Call Site 3 only — slot parsing), Placeholder Registry.
- **Trust boundary.** N/A — pre-retrieval, no data access yet.
- **Tenant boundary.** Lexicon is tenant-extensible but platform-default-backed.
- **Authentication/Authorization.** N/A.
- **Failure behavior.** A lexicon miss degrades diagnostic labeling only, never decomposition
  correctness (inherited from the research core's `Query Analyzer` design) — the split still
  proceeds whenever slot cardinality exceeds one.

### 2.13 Retrieval

- **Responsibility.** Hybrid (BM25 + dense) retrieval over the Proposition/Evidence layer,
  **with permission filtering applied before any candidate is scored or returned** —
  architecture §9.1/§9.2: unauthorized documents never enter the context window.
- **Data owned.** None — queries the Vector DB, Sparse Index, and Metadata Store.
- **Inputs.** `SubQuery`, security context (tenant, classification clearance, ABAC attributes).
- **Outputs.** A permission-filtered, ranked candidate proposition set.
- **APIs.** Internal: `retrieve(sub_query, security_context, k) -> Sequence[Proposition]`.
- **Dependencies.** Vector Database, Relational Database (Metadata Store), Authorization Engine
  (classification check), Embedder.
- **Trust boundary.** Application → Vector Store (boundary 6/7).
- **Tenant boundary.** Every query is scoped to `(tenant_id, ...)` at the index layer itself
  (database-level RLS / vector namespace isolation), not only in application code (architecture
  §4.4).
- **Authentication.** N/A.
- **Authorization.** Classification/ABAC filter is applied as a pre-filter on the candidate set,
  not a post-filter on results already handed to the LLM.
- **Failure behavior.** On index unavailability, returns empty results with an explicit
  "retrieval unavailable" signal — the orchestration layer abstains rather than guessing.

### 2.14 Reranking

- **Responsibility.** Cross-encoder precision pass over the fused, permission-filtered candidate
  set (unchanged from the research core's design).
- **Data owned.** None.
- **Inputs.** Filtered candidate propositions.
- **Outputs.** Top-k reranked propositions.
- **APIs.** Internal: `rerank(sub_query, propositions, k) -> Sequence[Proposition]`.
- **Dependencies.** Reranker model.
- **Trust boundary.** None additional — operates only on already-authorized candidates
  (architecture §9.3: unauthorized documents cannot influence reranking, because they were never
  candidates).
- **Tenant boundary.** Inherited from the already-filtered input set.
- **Authentication/Authorization.** N/A — operates strictly downstream of §2.13's filter.
- **Failure behavior.** On model unavailability, degrades to the fused (unreranked) order rather
  than failing the whole request — quality degradation, not an outage.

### 2.15 Proposition / Evidence Layer

- **Responsibility.** Owns the canonical Proposition record (architecture §5.1, amended per §4.2
  of this plan's parent document to add `tenant_id`), the FFF rewrite step (Call Site 4, guarded),
  and the Fact-set/Query Pairer.
- **Data owned.** `propositions`, `embeddings`/index references (Part 6 details the full schema).
- **Inputs.** Reranked propositions, sub-query.
- **Outputs.** Paired `FFF` records for the Generator.
- **APIs.** Internal.
- **Dependencies.** LLM Gateway (Call Site 4 — FFF Rewriter), Numeric/Period-Verbatim Guard.
- **Trust boundary.** LLM boundary — the guard is the enforcement point stopping an
  unsourced/mislabeled figure from reaching generation.
- **Tenant boundary.** `(tenant_id, proposition_id)` composite key throughout (three-index
  invariant, architecture §4.3).
- **Authentication/Authorization.** N/A — operates on already-authorized evidence.
- **Failure behavior.** A proposition failing the verbatim guard is dropped, never repaired or
  passed through (inherited rule).

### 2.16 LLM Gateway

- **Responsibility.** The single seam through which every LLM call in the platform passes —
  implements the OpenAI Agents SDK integration (architecture §2.2), enforces the call-site
  registry (§2.6), and is the only component permitted to hold a provider SDK dependency
  (inherited rule, `tests/test_no_direct_sdk_imports.py`, generalized platform-wide).
- **Data owned.** None persistent — reads Model Registry and Prompt Registry per call.
- **Inputs.** A call-site identifier, a structured payload, tenant's model/provider configuration.
- **Outputs.** A schema-validated structured response, or a bounded-retry-then-fallback failure.
- **APIs.** Internal: `complete(call_site, payload, tenant_context) -> StructuredResponse`.
- **Dependencies.** Model Registry (§2.17), Prompt Registry (§2.18), Secrets Management (tenant's
  LLM key, resolved by reference), Agents SDK provider adapters.
- **Trust boundary.** Application → LLM (boundary 8) — this component is the boundary's technical
  implementation.
- **Tenant boundary.** Resolves the tenant's own LLM provider/key strictly — Bank A's key can
  never be used to serve Bank B's request (architecture §22.1); enforced by requiring
  `tenant_id` on every call and resolving credentials only from that tenant's secret references.
- **Authentication.** N/A internally — authenticates *to* the LLM provider using the tenant's
  credentials.
- **Authorization.** Enforces the call-site registry — a call site not in the registry cannot be
  invoked, closing off the possibility of an undocumented seventh (or Nth) LLM touchpoint being
  added silently.
- **Failure behavior.** Schema-invalid or guard-failing output → bounded retry (max 2) →
  deterministic fallback that drops the item, never invents one (inherited rule, generalized to
  every call site).

### 2.17 Model Registry

- **Responsibility.** Tracks provider, exact pinned version, approved modules/tenants, evaluation
  results, and retirement status for every model in production use (architecture §18.1).
- **Data owned.** `models`, `model_versions`.
- **Inputs.** New model version proposals (from engineering), per-tenant approval decisions.
- **Outputs.** The approved model/version for a given tenant + call site, to the LLM Gateway.
- **APIs.** `GET /models/{call_site}/{tenant_id}`, `POST /models/{id}/approve` (per tenant),
  `POST /models/{id}/retire`.
- **Dependencies.** Configuration Service (versioned registry pattern, §2.30), Evaluation Harness
  (gate-suite results gate promotion).
- **Trust boundary.** Internal governance boundary — no model reaches production for a tenant
  without recorded approval.
- **Tenant boundary.** Approval is per-tenant; a bank can refuse a new model version.
- **Authentication/Authorization.** Platform Admin Console only for registering a new model
  version; Bank Admin Portal for that tenant's approval decision.
- **Failure behavior.** If a requested call-site/tenant combination has no approved model, the
  LLM Gateway call fails closed rather than falling back to an unapproved default.

### 2.18 Prompt Registry

- **Responsibility.** Version-controls every call site's prompt (architecture §18.2). Enforces
  that tenant customization is limited to an approved extension point, never a full rewrite.
- **Data owned.** `prompts`, `prompt_versions`.
- **Inputs.** New prompt version proposals, tenant-specific terminology extensions.
- **Outputs.** The exact prompt version to use for a given call site + tenant, to the LLM Gateway.
- **APIs.** `GET /prompts/{call_site}/{tenant_id}`, `POST /prompts/{id}/versions`.
- **Dependencies.** Configuration Service.
- **Trust boundary.** Internal governance boundary — a tenant cannot weaken a structural guard
  (numeric-verbatim, identity-never-claimed) via this mechanism, because those guards are
  enforced in code (§2.15, §2.6), not by prompt wording.
- **Tenant boundary.** Extension content is tenant-scoped; the base prompt is platform-owned.
- **Authentication/Authorization.** Platform engineering for base prompt changes (through Change
  Management, Part 16); Bank Admin Portal for the approved extension point only.
- **Failure behavior.** An extension that fails validation (e.g., attempts to override a
  structural instruction) is rejected at submission time, not silently accepted.

### 2.19 Guardrails

- **Responsibility.** Houses the numeric-verbatim guard, period-verbatim guard, operand-verbatim
  guard, and the deterministic numeric comparator used by CoVe — all as pure, non-LLM code
  (inherited rule, Principle 1).
- **Data owned.** None.
- **Inputs.** LLM-generated FFFs, calculation plans, verification claims.
- **Outputs.** Pass/fail/reject decisions.
- **APIs.** Internal, called synchronously by the Proposition/Evidence Layer and CoVe.
- **Dependencies.** None beyond the Proposition layer's data.
- **Trust boundary.** The technical enforcement point for "an LLM never touches a digit."
- **Tenant boundary.** N/A — stateless logic.
- **Authentication/Authorization.** N/A.
- **Failure behavior.** Any guard failure results in the item being dropped, never repaired.

### 2.20 Confidence Engine

- **Responsibility.** Computes the composite confidence score (architecture §13.1) from
  retrieval/rerank scores, guard results, CoVe match rate, extraction tier, OCR confidence, and
  calculation verification. Enforces the two-level threshold (platform floor + tenant policy,
  §13.2).
- **Data owned.** Confidence policy per tenant/category (`configuration_versions`, see Part 16).
- **Inputs.** Signals listed above, from RAG Orchestration.
- **Outputs.** A confidence score attached to the answer; a below-threshold signal to the
  Escalation Service.
- **APIs.** Internal: `score(signals) -> ConfidenceResult`.
- **Dependencies.** Configuration Service (thresholds), Human Escalation Service.
- **Trust boundary.** N/A.
- **Tenant boundary.** Thresholds are tenant-configurable but clamped to the platform floor —
  enforced here, not merely documented.
- **Authentication/Authorization.** N/A.
- **Failure behavior.** If any required signal is missing, confidence is computed conservatively
  (treated as the lowest-confidence case for that signal), never optimistically.

### 2.21 Calculation Engine

- **Responsibility.** Implements the Calculation Planner call site and the versioned formula
  registry (architecture §2.7). The LLM selects an operation and operands; this engine executes
  the operation deterministically.
- **Data owned.** The formula registry itself (versioned, reviewed additions only).
- **Inputs.** A calculation plan (`{operation, operand_proposition_ids}`) from the LLM Gateway.
- **Outputs.** A `derived` proposition with recorded operation and inputs, or an abstention if the
  operation is unregistered or operands fail the verbatim guard.
- **APIs.** Internal: `execute(plan) -> DerivedProposition | Abstain`.
- **Dependencies.** Guardrails (§2.19, operand-verbatim check), Proposition/Evidence Layer.
- **Trust boundary.** The technical enforcement point for "the LLM never performs authoritative
  arithmetic."
- **Tenant boundary.** N/A — stateless logic over already-tenant-scoped propositions.
- **Authentication/Authorization.** N/A.
- **Failure behavior.** Unregistered operation or ambiguous convention → abstain, never guess.

### 2.22 Agent Orchestrator

- **Responsibility.** Executes the Agentic Research Mode lane (architecture §2.1) for modules
  assigned to it (§2.3): Regulatory Intelligence, Fraud Investigation Support, and the Management
  pillar's exploratory modules. Enforces the execution-budget object (Part 9 defines this in
  full) — max tool calls, max runtime, max cost, max recursion, max retries, all enforced here in
  code, never left to prompt instruction.
- **Data owned.** `agent_tasks`, `agent_steps` (full trajectory, replayable for audit).
- **Inputs.** A task request plus security context, only from a module already assigned Agentic
  mode.
- **Outputs.** A completed result or an explicit "incomplete — limit reached" signal.
- **APIs.** Internal: `run_task(task, security_context, budget) -> AgentResult`.
- **Dependencies.** LLM Gateway, Tool/MCP Gateway, Confidence Engine, Human Escalation Service.
- **Trust boundary.** LLM → Tool (boundary 13), internally orchestrated.
- **Tenant boundary.** Every tool call the agent makes carries the same tenant-scoped security
  context as the originating request — the agent cannot change tenant mid-task.
- **Authentication/Authorization.** Every tool selection is checked against the Authorization
  Engine independently of the LLM's choice (architecture §13.8 — object-level authorization
  enforced at the tool boundary, not trusted from the agent).
- **Failure behavior.** On budget breach, stops and returns partial results with an explicit
  incomplete flag — never silently continues past a limit.

### 2.23 Tool / MCP Gateway

- **Responsibility.** The single point through which both lanes invoke tools. Validates every
  tool call's authorization independently of what the LLM/agent supplied (architecture §13.8),
  and validates every tool *response* against its expected schema before it is trusted
  (architecture §8.4, §10.11) — a compromised or malformed tool response is rejected, not passed
  through.
- **Data owned.** `tools`, `tool_permissions`.
- **Inputs.** A tool-call request (tool name, arguments) from the Fixed pipeline or Agent
  Orchestrator.
- **Outputs.** A validated tool result, or a rejection.
- **APIs.** Internal: `invoke(tool_name, arguments, security_context) -> ToolResult`.
- **Dependencies.** Authorization Engine, Connector Framework (§2.24), Connector Capability
  Manifest (per-tenant, per-connector).
- **Trust boundary.** LLM → Tool (boundary 13); Tool → External System (boundary 14) begins here.
- **Tenant boundary.** MCP server instances are deployed per tenant (architecture §2.4, §10.11) —
  this Gateway routes to the correct tenant's MCP instance, never a shared one.
- **Authentication.** Presents the platform's own service identity to the connector, never the
  end user's raw credentials.
- **Authorization.** Object-level check performed here, independent of the LLM's claim about which
  account/record it wants — this is the concrete enforcement point for architecture §13.8.
- **Failure behavior.** An unauthorized tool call is rejected before it reaches the connector; a
  malformed response is rejected before it reaches the LLM.

### 2.24 Connector Framework

- **Responsibility.** Implements the eleven-dimension connector profile (architecture §10.1):
  transport, authentication, authorization, credential acquisition, request binding, headers,
  encryption, request format, session lifecycle, request lifecycle, response handling. Full detail
  in Part 10.
- **Data owned.** `connectors`, `connector_credentials` (credentials stored as references only,
  per Part 5).
- **Inputs.** A validated tool-call request from the Tool/MCP Gateway.
- **Outputs.** A normalized response, regardless of the underlying protocol.
- **APIs.** Internal per-connector; externally, whatever protocol that specific bank endpoint
  requires (REST/SOAP/GraphQL/gRPC/webhook).
- **Dependencies.** Secrets Management, Bank Middleware / CBS (external).
- **Trust boundary.** Application → MCP (boundary 9) on the inbound side; MCP → Bank system
  (boundary 10) on the outbound side.
- **Tenant boundary.** Every connector instance and its credentials are tenant-scoped, isolated at
  the database and secrets-manager layer.
- **Authentication.** Performs whichever auth strategy that connector's profile specifies.
- **Authorization.** Consults the Connector Capability Manifest — an operation not declared
  supported is never attempted, independent of RBAC (architecture §10.6).
- **Failure behavior.** Circuit breaker and backoff on every call; a failed/unreachable system
  returns "temporarily unavailable," never a guess (architecture §10.9).

### 2.25 Bank Middleware / CBS (External)

- **Responsibility.** Not part of the platform — the bank's own system of record. The Connector
  Framework's tool interface (`get_account_balance`, etc.) remains identical whether it points at
  a CBS directly or at a bank's middleware in front of it (architecture §10.5).
- **Data owned.** Everything — this is the authoritative source for account, transaction, and
  customer data (Principle 2).
- **Trust boundary.** MCP → Bank system (boundary 10) — treated as an external, only
  partially-trusted system whose responses are still schema-validated (§2.23).
- **Tenant boundary.** One per bank, by definition.
- **Failure behavior.** Platform-side: see §2.24. Bank-side: outside platform control.

### 2.26 Human Escalation Service

- **Responsibility.** Implements the multi-factor escalation trigger, routing, reassignment chain,
  and fail-closed timeout behavior (architecture §9). Prevents reviewer self-approval (§9.4).
- **Data owned.** `human_escalations`, `approvals`.
- **Inputs.** Escalation triggers from Confidence Engine, Guardrails (CoVe failure), Agent
  Orchestrator (budget breach on a high-risk task), or explicit user request.
- **Outputs.** A routed escalation to the correct queue; an approve/reject/timeout decision back
  to the originating pipeline.
- **APIs.** `POST /escalations`, `POST /escalations/{id}/approve`, `POST
  /escalations/{id}/reject`.
- **Dependencies.** Authorization Engine (reviewer eligibility, self-approval check), Queue/Event
  Bus (routing), Audit Service.
- **Trust boundary.** Internal governance boundary.
- **Tenant boundary.** Queues and reviewer pools are tenant-scoped.
- **Authentication/Authorization.** A reviewer's access to the escalation's underlying content is
  itself object-level checked (architecture §9.6); a reviewer cannot approve an action where
  `requester_id == reviewer_id` or where they are the subject of the action.
- **Failure behavior.** On timeout with no response, defaults to pending or safe refusal per
  tenant policy — **never automatic approval** (architecture Principle 5, §9.3).

### 2.27 Audit Service

- **Responsibility.** Implements the append-only, hash-chained audit log (architecture §14.2,
  fully detailed in Part 14). Captures every retrieval, tool call, LLM call, permission check,
  escalation decision, configuration change, and authentication event.
- **Data owned.** `audit_events`, `audit_chain`.
- **Inputs.** Events emitted by every other service in the platform.
- **Outputs.** Queryable, tamper-evident audit trail; compliance evidence exports.
- **APIs.** `POST /audit/events` (internal, service-to-service only), `GET
  /audit/events` (read access itself permission-scoped).
- **Dependencies.** Relational Database or a dedicated append-only store (implementation choice,
  Part 14 distinguishes this explicitly), Secrets Management (chain-anchor signing, if used).
- **Trust boundary.** The evidentiary backbone behind every other trust boundary in Part 3.
- **Tenant boundary.** Audit events are tenant-scoped; no delete capability exists for any role
  (architecture §14.3).
- **Authentication/Authorization.** Write access: internal service identity only, never a
  user-facing API. Read access: permission-scoped per architecture §14.3.
- **Failure behavior.** **Fails loud, not silent** — if the Audit Service is unreachable, the
  calling service's action does not silently proceed unaudited for anything security-critical
  (authorization decisions, escalation approvals, tool calls); the action is blocked until audit
  writes succeed, or degraded to a queued-with-guaranteed-delivery pattern via the Queue/Event Bus
  rather than dropped.

### 2.28 PII / Tokenization Service

- **Responsibility.** Detects and redacts/tokenizes PII before content reaches embedding, the LLM,
  logs, traces, or the cache (architecture §7). Owns the reversible-tokenization mapping vault.
- **Data owned.** The session-scoped, encrypted mapping vault (`<ACCOUNT_1> ↔ 0123456789`).
  **Never owns a durable store of raw PII beyond the vault's own retention window.**
- **Inputs.** Raw text (user query, uploaded document, tool response) before it reaches any
  downstream component.
- **Outputs.** Tokenized text plus a resolvable mapping, scoped to the requesting session.
- **APIs.** Internal: `redact(text, tenant_policy) -> (TokenizedText, MappingHandle)`,
  `resolve(token, mapping_handle, security_context) -> RawValue` (callable only from within the
  Connector Framework at the point of an actual bank API call — never from the LLM Gateway or
  Agent Orchestrator).
- **Dependencies.** Secrets Management (vault encryption keys), Configuration Service (tenant PII
  policy).
- **Trust boundary.** The technical enforcement point for Principle 4 with respect to PII
  specifically.
- **Tenant boundary.** Policy and vault are both tenant-scoped.
- **Authentication/Authorization.** `resolve()` is authorization-checked against the specific
  connector call requesting it — a general request to "reveal the real value" is not a valid call
  shape.
- **Failure behavior.** On any doubt about whether content contains PII, the conservative choice
  (redact) is taken — never the permissive one.

### 2.29 Secrets Management

- **Responsibility.** Holds every credential in the platform — LLM provider keys, connector
  credentials, IdP client secrets, the PII vault's encryption keys, the license-signing private
  key — as references resolved only at the point of use, never persisted or logged in plaintext
  anywhere else.
- **Data owned.** All secret material, tenant-scoped except the license-signing key (platform-
  global, held with the strictest access policy in the system, architecture §8.2).
- **Inputs.** Secret registration during connector/tenant onboarding.
- **Outputs.** Resolved secret values, only to the specific internal caller authorized for that
  reference, never returned to any client-facing API.
- **APIs.** Internal only: `resolve(secret_reference, caller_identity) -> SecretValue`.
- **Dependencies.** Underlying secrets backend (implementation choice — HashiCorp Vault, cloud
  KMS/Secrets Manager, or equivalent, per architecture §5.2's adapter pattern).
- **Trust boundary.** The technical enforcement point behind every "never logged, never in the
  LLM context, never in a config file" rule in this document.
- **Tenant boundary.** Tenant-scoped secret namespaces; a resolution request for Bank A's
  reference cannot be satisfied using Bank B's namespace, enforced at this service, not just by
  convention.
- **Authentication/Authorization.** Every resolution request is checked against a caller allowlist
  (which internal service, for which reference) — not an open lookup.
- **Failure behavior.** On any resolution failure, the calling connector/service fails closed —
  there is no default or fallback credential.

### 2.30 Configuration Service

- **Responsibility.** Owns the versioned-registry pattern used by Model Registry, Prompt Registry,
  Guardrail configuration, Connector definitions, and tenant policy (confidence thresholds,
  escalation rules, PII policy) — implements the Draft → Validation → Evaluation → Approval →
  Production → Rollback lifecycle (Part 16).
- **Data owned.** `configuration_versions`.
- **Inputs.** Proposed configuration changes from Bank Admin Portal or Platform Admin Console.
- **Outputs.** The active configuration version for any given tenant/component, plus history.
- **APIs.** `POST /config/{domain}/draft`, `POST /config/{domain}/{version}/activate`,
  `POST /config/{domain}/rollback`.
- **Dependencies.** Policy Engine (§2.31), Audit Service (every change is audited, architecture
  §18.4).
- **Trust boundary.** Internal governance boundary.
- **Tenant boundary.** Tenant-scoped configuration is isolated at the database layer, same as
  every other tenant record.
- **Authentication/Authorization.** Sensitive domains (confidence floor, security policy) require
  two-person authorization to activate (architecture §17.1/§17.2).
- **Failure behavior.** A new version failing validation (e.g., a confidence threshold below the
  platform floor) is rejected at draft time, never reaches activation.

### 2.31 Policy Engine

- **Responsibility.** Evaluates ABAC/PBAC policy documents (architecture §6.5) as a distinct
  concern from RBAC role checks — called by the Authorization Engine, not a replacement for it.
- **Data owned.** Policy documents (`policies`).
- **Inputs.** Security context, resource attributes, a policy set.
- **Outputs.** Allow/deny with matched policy reference.
- **APIs.** Internal: `evaluate(context, resource, policies) -> PolicyDecision`.
- **Dependencies.** Configuration Service.
- **Trust boundary.** Sits behind the Authorization Engine (§2.6).
- **Tenant boundary.** Policies are tenant-authored, platform-validated at draft time.
- **Authentication/Authorization.** N/A — this component provides a decision, doesn't authenticate.
- **Failure behavior.** No matching policy → deny, not allow (default-deny posture).

### 2.32 Document Ingestion

- **Responsibility.** Path A/Path B intake (architecture §12.1), full ingestion hardening
  (§12.4). Detailed fully in Part 12.
- **Data owned.** Transient upload state; final output lands in Object Storage / Relational
  Database / Vector Database.
- **Inputs.** Uploaded files (PDF, DOCX, XLSX, CSV, images, XBRL) or structured API/DB extracts.
- **Outputs.** Classified, versioned, embedded propositions.
- **APIs.** `POST /documents/upload`, `GET /documents/{id}/status`.
- **Dependencies.** Malware Scanning (§2.33), OCR (§2.34), PII/Tokenization Service, LLM Gateway
  (Call Sites 1–2), Embedder.
- **Trust boundary.** Document → Ingestion Pipeline (boundary 12).
- **Tenant boundary.** Every ingested artifact is tagged `tenant_id` at the point of upload,
  before any processing.
- **Authentication/Authorization.** Upload requires the uploading user's authorization for the
  target classification level.
- **Failure behavior.** A malformed, malicious, or oversized document is rejected before it
  reaches parsing — never partially processed and silently indexed.

### 2.33 Malware Scanning

- **Responsibility.** Scans every uploaded file before it reaches the parsing pipeline; strips or
  rejects macro-bearing Office documents (architecture §12.4).
- **Data owned.** None persistent.
- **Inputs.** Raw uploaded file.
- **Outputs.** Clean/reject verdict.
- **APIs.** Internal, called synchronously by Document Ingestion before any other step.
- **Dependencies.** A scanning engine (implementation choice).
- **Trust boundary.** The first checkpoint inside boundary 12.
- **Tenant boundary.** N/A — stateless.
- **Authentication/Authorization.** N/A.
- **Failure behavior.** Any scan failure (engine unavailable, ambiguous result) is treated as a
  reject, not a pass — fail closed.

### 2.34 OCR

- **Responsibility.** Layout- and table-aware OCR for scanned documents (inherited from the
  research core's PaddleOCR/PP-Structure design), now genuinely exercised on synthesized scanned
  fixtures (architecture §12.1), not merely unit-tested.
- **Data owned.** None persistent.
- **Inputs.** Scanned pages with no extractable text layer.
- **Outputs.** Extracted text plus a mandatory `ocr_confidence` field per proposition.
- **APIs.** Internal, invoked by Document Ingestion.
- **Dependencies.** OCR model.
- **Trust boundary.** N/A — internal processing step.
- **Tenant boundary.** N/A — stateless per-document processing.
- **Authentication/Authorization.** N/A.
- **Failure behavior.** Low-confidence OCR output is tagged, not discarded — the Confidence Engine
  and Abstention Gate treat low `ocr_confidence` conservatively downstream.

### 2.35 Object Storage

- **Responsibility.** Holds raw uploaded documents, exported audit/compliance evidence packages,
  and any binary artifact.
- **Data owned.** Raw files.
- **Inputs/Outputs.** Standard object store semantics.
- **APIs.** Internal only, accessed via Document Ingestion and export tooling — never directly
  client-facing.
- **Dependencies.** Encryption (at rest, architecture §8.2), Secrets Management (encryption keys).
- **Trust boundary.** Application → Object Storage (boundary 7).
- **Tenant boundary.** Separate prefixes/buckets per tenant (architecture §4.4).
- **Authentication/Authorization.** No direct external access; all access mediated by an owning
  service.
- **Failure behavior.** Write failures propagate as ingestion failures, not silently dropped
  documents.

### 2.36 Relational Database

- **Responsibility.** System of record for every structured entity in Part 5 — tenants, users,
  roles, conversations, propositions' metadata, connectors, licenses, audit events, and more.
- **Data owned.** Everything in Part 5's schema.
- **Trust boundary.** Application → Database (boundary 5).
- **Tenant boundary.** Row-level security policies tied to the connection's tenant context — the
  primary defense-in-depth layer beneath application-level filtering (architecture §4.4).
- **Authentication/Authorization.** Connection-level credentials scoped per service; RLS enforces
  tenant isolation independent of application code correctness.
- **Failure behavior.** Standard transactional guarantees; failures propagate to the calling
  service, which fails closed per its own specification above.

### 2.37 Vector Database

- **Responsibility.** Dense index for hybrid retrieval, keyed `(tenant_id, proposition_id)`.
- **Data owned.** Embeddings and their proposition-id linkage.
- **Trust boundary.** Application → Vector Store (boundary 6).
- **Tenant boundary.** Physically or logically separate namespaces per tenant (architecture §4.4)
  — not a single shared collection with a filter field.
- **Authentication/Authorization.** Query-time tenant scoping enforced by the Retrieval component
  (§2.13) and, where the backend supports it, by the vector store's own namespace access control.
- **Failure behavior.** Retrieval degrades to abstention on unavailability (§2.13).

### 2.38 Cache

- **Responsibility.** Semantic/response cache, session cache, connector response cache.
- **Data owned.** Cached entries, keyed per architecture §14.5/§23.5: tenant + user/scope +
  document version + query — **never** a bare query string as the key.
- **Trust boundary.** Application-internal.
- **Tenant boundary.** Tenant is part of every key; cross-tenant cache collision is structurally
  impossible given the key design.
- **Authentication/Authorization.** N/A — a cache hit still passes through the same
  authorization-scoped key, so an unauthorized reader cannot retrieve another user's cached
  answer.
- **Failure behavior.** Cache unavailability degrades to a cache-miss (recompute), never a stale
  or incorrect hit. Sensitive transactional queries bypass the cache entirely (architecture
  §11.5).

### 2.39 Queue / Event Bus

- **Responsibility.** Asynchronous work (ingestion pipelines, batch evaluation, escalation
  routing, audit-write guarantees) decoupled from the live request path.
- **Data owned.** In-flight messages.
- **Trust boundary.** Internal.
- **Tenant boundary.** Messages carry `tenant_id`; consumers apply the same authorization checks
  as any synchronous path — asynchronous processing does not bypass authorization.
- **Authentication/Authorization.** Producer/consumer identity is service-level, not user-level.
- **Failure behavior.** Dead-letter queues for repeatedly failing messages; no message is silently
  dropped.

### 2.40 Observability

- **Responsibility.** Logs, metrics, and traces (Langfuse-based for LLM/agent traces, a
  conventional stack for infrastructure metrics) — kept distinct per Part 18. Redaction occurs
  before export, not after (architecture §15.1).
- **Data owned.** Time-series/log data, tenant-tagged.
- **Trust boundary.** Anything that could leak tenant content is treated as sensitive by default.
- **Tenant boundary.** Trace/log access is scoped through the same identity/authorization system
  as everything else — not a separately unguarded tool.
- **Authentication/Authorization.** Platform Admin Console access is itself least-privilege scoped
  (architecture §17.2).
- **Failure behavior.** Observability outages do not affect the request path (best-effort,
  fire-and-forget from the perspective of the serving components) but are themselves alerted on.

### 2.41 Billing / Licensing Service

- **Responsibility.** Issues and verifies signed entitlement documents (architecture §22.2),
  tracks usage for reporting (never for third-party consumption billing, which the platform never
  charges for), and enforces license validity per deployment type (§22.9).
- **Data owned.** `licenses`, `feature_entitlements`.
- **Inputs.** Plan Registry data, tenant plan assignment.
- **Outputs.** A signed entitlement document per tenant; periodic license-validation heartbeat
  responses for on-prem deployments that permit it.
- **APIs.** `POST /licenses/issue`, `GET /licenses/{tenant_id}/verify`.
- **Dependencies.** Plan Registry (§2.42), Secrets Management (license-signing private key).
- **Trust boundary.** The technical backbone of §22.9's enforcement posture.
- **Tenant boundary.** One entitlement document per tenant.
- **Authentication/Authorization.** Issuance is Platform Admin Console only.
- **Failure behavior.** An unverifiable or expired entitlement causes the platform to enforce the
  entitlement's last-known-valid state or a defined grace period, per deployment-type policy —
  never silent unlimited access.

### 2.42 Plan Registry

- **Responsibility.** The fully configurable pricing/packaging store (architecture §22.3) — tier
  names, prices, included modules, seat/capacity policy, deployment options, support levels, all
  editable by the platform vendor at any time without a code release.
- **Data owned.** `plans`.
- **Inputs.** Vendor-side edits via Platform Admin Console.
- **Outputs.** Plan templates consumed by the Billing/Licensing Service when issuing an
  entitlement.
- **APIs.** `GET /plans`, `POST /plans` (Platform Admin only), `PATCH /plans/{id}`.
- **Dependencies.** None.
- **Trust boundary.** Platform Admin Console only.
- **Tenant boundary.** N/A — plans are platform-global templates; an individual tenant's
  entitlement is a separate, possibly customized, document.
- **Authentication/Authorization.** Platform Admin Console only.
- **Failure behavior.** N/A — a configuration store; failures are data-integrity concerns handled
  by Configuration Service's versioning (§2.30).

### 2.43 Admin Portal (Bank-facing)

- **Responsibility.** Implements every section of architecture §17.1 — Identity & Customer
  Authentication, Core Banking/Connectors, Security & Governance, Users & Roles, Data, Audit &
  Entitlement, Custom Requests, Testing.
- **Data owned.** None directly — a UI over Tenant Service, Connector Framework, Configuration
  Service, and User/Session Service.
- **Trust boundary.** Tenant → Tenant isolation (boundary 4) is enforced here at the UI layer as
  defense-in-depth, and independently at the database layer beneath every service it calls.
- **Tenant boundary.** Strictly scoped to the authenticated bank admin's own tenant, including
  against URL manipulation attempts (architecture §17's explicit callout).
- **Authentication/Authorization.** Own internal least-privilege model — no single flat "Bank
  Admin" role; sensitive changes require a second admin's confirmation (architecture §17.1).
- **Failure behavior.** Same as §2.1 — never exposes backend error detail.

### 2.44 Support / Break-glass Tooling

- **Responsibility.** Implements architecture §17.2's Platform Operator access boundary — redacted
  ticket/session lookup by default, explicit escalation path into break-glass (tenant consent,
  time-bound, fully audited, visible in the tenant's own portal) for genuine investigation needs.
- **Data owned.** None directly — a controlled, redacted view over Conversation Service, Audit
  Service.
- **Trust boundary.** Platform Operator → Tenant (boundary 11) — this is the concrete
  implementation of that boundary's constraint.
- **Tenant boundary.** Zero default access; break-glass grants are tenant- and case-specific,
  time-limited.
- **Authentication/Authorization.** Requires the requesting operator's own role to permit
  break-glass requests at all (§17.2's internal least-privilege rule), plus the tenant's consent
  workflow.
- **Failure behavior.** A break-glass request without tenant consent is denied, not queued
  indefinitely as a pending override.

### 2.45 Backup / DR

- **Responsibility.** Tenant-scoped backup and restore for every stateful component, per the
  RPO/RTO targets in architecture §19 (detailed fully in Part 19).
- **Data owned.** Backup artifacts, encrypted, tenant-tagged.
- **Trust boundary.** Same as the data it backs up.
- **Tenant boundary.** One tenant's restore process cannot touch another tenant's data
  (architecture §19.3) — enforced by keeping backups tenant-partitioned, never a single combined
  snapshot restored wholesale.
- **Authentication/Authorization.** Restore operations are Platform Admin Console only, logged.
- **Failure behavior.** See Part 19 for per-dependency failure/recovery behavior — external
  dependency failures (LLM, CBS, IAM) are explicitly *not* treated as platform disaster states
  (architecture §19.2).

---

## 3. Cross-Component Rules That Apply Platform-Wide

These are restated here because they are not any single component's responsibility — they are
properties of how components are wired together, and every component specification above assumes
them:

1. **No service performs its own ad hoc authorization logic.** Every authorization decision routes
   through the Authorization Engine (§2.6).
2. **No service holds a provider SDK dependency except the LLM Gateway.** (inherited rule,
   platform-wide).
3. **No secret is ever passed as a value between services.** Only references, resolved by
   Secrets Management (§2.29) at the point of use.
4. **No component fabricates a response on a dependency failure.** Every failure mode specified
   above resolves to abstention, rejection, or an explicit "unavailable" signal — never a guess.
5. **Every write to a security-relevant record is audited**, and the Audit Service's own failure
   mode (§2.27) ensures this isn't optional under load or partial outage.

---

*End of Part 1. Part 2 (Deployment Architecture) is the next document in this series.*
