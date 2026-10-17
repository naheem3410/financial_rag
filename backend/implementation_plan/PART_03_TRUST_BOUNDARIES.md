# Master Technical Architecture and Implementation Plan
## Part 3 — Trust Boundaries

Every boundary below is enforced by a specific component named in Part 1. This part exists to make
each boundary's controls explicit and checkable in one place, rather than scattered across
component specs.

---

## Boundary 1 — Internet → Platform

- **Authentication.** None yet possible — this boundary is crossed before any identity exists.
- **Authorization.** None — the only decision here is "is this a reachable route at all."
- **Data filtering.** WAF rules; malformed/oversized requests rejected before reaching the API
  Gateway.
- **Encryption.** TLS 1.2+ terminated at the load balancer (Part 2).
- **Validation.** Schema validation on every request body at the API Gateway (architecture §8.8).
- **Logging.** Access logs, redacted of any credential material before persistence.
- **Failure behavior.** Fails closed — an unparseable or oversized request is rejected, never
  passed through partially.

## Boundary 2 — Customer → Platform

- **Authentication.** Customer Authentication Flow Engine (Part 1 §2.4) — BVN/OTP/password/MFA
  per tenant-configured flow, or federated OIDC/SAML where a bank offers it.
- **Authorization.** None at this boundary itself — authorization begins once an identity is
  established and a request targets a specific resource (Boundary 5 onward).
- **Data filtering.** PII/Tokenization Service redacts any identifier present in free-text input
  before it reaches any downstream service, including this boundary's own logs.
- **Encryption.** TLS in transit; the flow's transient state (BVN, OTP) is never persisted beyond
  the flow's completion or timeout.
- **Validation.** Flow-step schema validation; rate limiting keyed on tenant + customer identity +
  device + IP + session + endpoint (architecture §6.3).
- **Logging.** Flow attempts logged with identifiers tokenized, never raw.
- **Failure behavior.** Repeated failure triggers lockout per tenant policy; a downstream bank
  verification endpoint being unavailable reports failure honestly, never assumes success.

## Boundary 3 — Employee → Platform

- **Authentication.** Federated to the bank's own IAM (Azure AD/Okta/SAML/OIDC) or
  platform-managed identity fallback (Part 1 §2.3).
- **Authorization.** Identity claims mapped to platform roles/attributes via a tenant-configurable
  mapping table; RBAC baseline evaluated by the Authorization Engine on every subsequent request.
- **Data filtering.** N/A at this boundary — filtering happens at data-access boundaries (5, 6, 7)
  downstream.
- **Encryption.** TLS in transit; SAML assertions/OIDC tokens validated for signature and expiry.
- **Validation.** IdP response signature verification; rejects any assertion not issued by the
  tenant's configured IdP.
- **Logging.** Every authentication event logged (success and failure) with tenant and user
  tagging.
- **Failure behavior.** A federation failure results in login failure with a generic message —
  never a fallback to an unauthenticated session (Part 1 §2.3).

## Boundary 4 — Tenant → Tenant

- **Authentication.** N/A — this boundary is not crossed by any legitimate request; it exists to
  be tested against, not designed to be traversed.
- **Authorization.** Every data-access path independently re-derives tenant scope from the
  authenticated session context — never from a client-supplied tenant identifier.
- **Data filtering.** Defense-in-depth per architecture §4.4: application-level tenant context +
  database row-level security + vector namespace isolation + object storage prefix isolation +
  cache-key tenant inclusion + configuration-store isolation. No single layer is trusted alone.
- **Encryption.** Tenant-scoped encryption keys where BYOK is in effect (dedicated/on-prem).
- **Validation.** Adversarial test suite (Part 23) explicitly attempts cross-tenant retrieval and
  asserts zero results as a release gate, not a one-time check.
- **Logging.** Any cross-tenant access *attempt* (a request whose resolved tenant does not match
  the resource's tenant) is logged as a security event and alerted on, not merely denied silently.
- **Failure behavior.** Deny, always — there is no legitimate code path that crosses this
  boundary, so any attempt is treated as an anomaly requiring investigation, not a normal
  authorization denial.

## Boundary 5 — Application → Database

- **Authentication.** Service-level database credentials, one identity per service, never a
  shared superuser credential used across services.
- **Authorization.** Row-Level Security policies keyed to the connection's resolved tenant
  context, evaluated by the database itself — independent of whether the calling service's own
  logic correctly applied a `WHERE tenant_id = ...` clause.
- **Data filtering.** RLS is the enforcement point; application-level filtering is a second,
  earlier layer, not a substitute for it.
- **Encryption.** Encryption at rest (architecture §8.2); TLS for the database connection itself.
- **Validation.** Parameterized queries only — no raw string concatenation into SQL anywhere in
  the platform (SQL injection defense, Part 17).
- **Logging.** Query-level audit for security-relevant tables (`users`, `permissions`,
  `connector_credentials`) routed to the Audit Service; general query logs are operational, not
  audit, and are themselves tenant-scoped for access purposes.
- **Failure behavior.** A connection failure propagates to the calling service, which fails closed
  per its own specification (Part 1).

## Boundary 6 — Application → Vector Store

- **Authentication.** Service-level credentials for the Retrieval component only — no other
  service queries the vector store directly.
- **Authorization.** Tenant namespace scoping enforced at the vector store layer itself, not only
  by the Retrieval component's query construction; classification/ABAC pre-filtering applied
  before candidates are scored (architecture §9.1/§9.2).
- **Data filtering.** Unauthorized documents never become candidates — filtering happens before
  scoring, not after (Part 1 §2.13).
- **Encryption.** Encryption at rest on the underlying storage backing the vector index.
- **Validation.** Embedding dimension consistency asserted at write time — a mismatched vector is
  refused, never silently written and corrupting the index (inherited from the research core's
  `DenseIndex` behavior).
- **Logging.** Retrieval queries logged (query text tokenized/redacted per PII policy) with
  tenant and result-count tagging for audit.
- **Failure behavior.** Unavailability degrades to abstention (Part 1 §2.13), not a fabricated
  answer.

## Boundary 7 — Application → Object Storage

- **Authentication.** Service-level credentials, scoped per tenant prefix where the backend
  supports prefix-level IAM policy.
- **Authorization.** No direct external access to object storage; every read/write is mediated by
  an owning service (Document Ingestion, Audit export tooling) that has already performed its own
  authorization check.
- **Data filtering.** Tenant-prefixed storage paths; a request resolving to a path outside the
  requester's tenant prefix is rejected at the mediating service, not merely at the storage layer.
- **Encryption.** At-rest encryption (architecture §8.2), with BYOK support for dedicated/on-prem.
- **Validation.** Uploaded content is scanned (Boundary 12) before being written to permanent
  storage.
- **Logging.** Every write/read logged with tenant, user, and purpose (upload, export, backup).
- **Failure behavior.** Write failures propagate as explicit ingestion/export failures, never
  silently dropped content.

## Boundary 8 — Application → LLM

- **Authentication.** The LLM Gateway authenticates *to* the provider using the tenant's own
  resolved credential (architecture §22.1) — never a platform-shared key used across tenants.
- **Authorization.** Call-site registry enforcement — only a registered call site can invoke the
  LLM Gateway at all (Part 1 §2.16).
- **Data filtering.** Context Builder (Part 1 §2.10) is the mandatory gateway for anything
  entering the prompt — no other component assembles LLM-facing context directly. PII is
  tokenized before this boundary is crossed (architecture §7.1).
- **Encryption.** TLS to the provider; tenant's own key never logged or cached in plaintext.
- **Validation.** Structured output schema validation on every response (inherited rule, §7.1 of
  `PROMPT_SPECIFICATIONS.md`, generalized).
- **Logging.** Prompt/response pairs traced via Langfuse **after** redaction, never before
  (architecture §15.1); the `reasoning` field is logged for audit but never parsed for facts.
- **Failure behavior.** Schema-invalid or guard-failing output → bounded retry (max 2) →
  deterministic fallback, drop never invent (inherited rule).

## Boundary 9 — Application → MCP

- **Authentication.** The platform's own service identity, never the end user's raw credentials,
  presented to the MCP layer.
- **Authorization.** Object-level check performed at the Tool/MCP Gateway (Part 1 §2.23),
  independent of what the LLM/agent supplied as the target identifier.
- **Data filtering.** Only narrow, task-specific tools are exposed — no generic execution surface
  (architecture §10.8).
- **Encryption.** Internal service-to-service TLS.
- **Validation.** Tool-call arguments schema-validated before dispatch to the Connector Framework.
- **Logging.** Every tool call logged with tenant, user, tool name, and arguments (PII-tokenized).
- **Failure behavior.** An unauthorized tool call is rejected before it reaches the connector.

## Boundary 10 — MCP → Bank System

- **Authentication.** Whichever auth strategy that specific connector's profile specifies
  (architecture §10.1) — resolved from tenant-scoped credential references.
- **Authorization.** Connector Capability Manifest constrains which operations are even attempted,
  independent of RBAC (architecture §10.6).
- **Data filtering.** N/A outbound — the bank's own system is the authority on what it returns
  (Principle 2); inbound (response) filtering happens at Boundary 14.
- **Encryption.** Whatever transport and payload encryption that connector's profile specifies
  (TLS, mTLS, AES-GCM/CBC, etc., architecture §10.1).
- **Validation.** Idempotency-key discipline on every write (architecture §10.1 item 6); circuit
  breaker and backoff on every call (architecture §10.9).
- **Logging.** Every connector call logged with tenant, connector, operation, and outcome.
- **Failure behavior.** A failed or unreachable system returns "temporarily unavailable" — never a
  guess dressed up as an answer.

## Boundary 11 — Platform Operator → Tenant

- **Authentication.** Platform Operator's own identity, distinct from any tenant's identity space.
- **Authorization.** **Zero default access.** Any access beyond the redacted default view of
  Support/Break-glass Tooling (Part 1 §2.44) requires an explicit break-glass grant: tenant
  consent, time-bound, and visible in the tenant's own admin portal (architecture §17.2).
- **Data filtering.** Redacted/masked views by default; full unmask only under an active
  break-glass grant, scoped to the specific case under investigation.
- **Encryption.** N/A additional — inherits the encryption of whatever data is being accessed.
- **Validation.** A break-glass request without a valid tenant-consent record is rejected at
  request time, not queued as a pending override.
- **Logging.** Every break-glass grant, its duration, its scope, and everything accessed during it
  is logged and surfaced to the tenant.
- **Failure behavior.** Deny by default; the absence of an active grant is itself the failure
  mode, not an edge case to handle separately.

## Boundary 12 — Document → Ingestion Pipeline

- **Authentication.** The uploading user's own authenticated session.
- **Authorization.** Upload requires the uploading user's authorization for the target
  classification level being assigned.
- **Data filtering.** Malware scanning and macro stripping/rejection before any parsing occurs
  (architecture §12.4); PII redaction before embedding.
- **Encryption.** At-rest encryption on the raw uploaded artifact.
- **Validation.** MIME-type validation, file-size ceiling, duplicate detection by content hash,
  malformed-document handling (rejected, not partially processed).
- **Logging.** Every upload, scan result, and classification decision logged with tenant and
  uploader.
- **Failure behavior.** A malicious, malformed, or oversized document is rejected before it
  reaches parsing — never partially indexed.

## Boundary 13 — LLM → Tool

- **Authentication.** N/A — the LLM does not authenticate; it issues a tool-call *request*, which
  is then independently authenticated and authorized by the Tool/MCP Gateway (Boundary 9).
- **Authorization.** This is the concrete enforcement point for "the LLM can never grant itself
  permissions" (architecture §25 Part 2 check) — the Gateway checks object-level authorization
  against the authenticated security context, never trusting the LLM's stated target.
- **Data filtering.** N/A — the filtering happens on the response, not the request, at this
  specific boundary.
- **Encryption.** N/A — internal call construction, not a network hop itself.
- **Validation.** Tool-call arguments must conform to the tool's declared schema; an
  out-of-schema call is rejected before dispatch.
- **Logging.** Every proposed tool call (including rejected ones) is logged for the Agent
  Orchestrator's trajectory record (architecture §12.7 of the original review — replayable for
  audit).
- **Failure behavior.** A rejected tool call returns an explicit denial to the agent's execution
  loop, consuming one unit of its retry budget (Part 9), never silently retried indefinitely.

## Boundary 14 — Tool → External System

- **Authentication/Authorization/Encryption/Validation/Logging.** Identical to Boundary 10 — this
  is the same boundary, named separately here because it is crossed by both the Fixed pipeline and
  the Agent Orchestrator, and both must be held to the identical standard.
- **Data filtering (response side, the part distinct from Boundary 10's outbound description).**
  Every response is schema-validated against the tool's expected contract before being trusted and
  treated as **untrusted data**, subject to the same "never an instruction" rule as retrieved
  documents (architecture §8.4, §10.11) — this is the concrete defense against a compromised or
  malicious external system attempting prompt injection through its response payload.
- **Failure behavior.** A response that fails schema validation is rejected outright — not
  passed through in degraded form, since a partially-trusted malformed response is a worse outcome
  than an explicit failure.

---

## Boundary Coverage Check

Every boundary above has at minimum: an authentication statement (even where "none" is the correct
answer, e.g. Boundary 1), an authorization statement, a data-filtering mechanism, an encryption
requirement, a validation step, a logging requirement, and a stated failure behavior that resolves
to deny/abstain/reject rather than an undefined or permissive default. No boundary in this list is
left as "trust and see."

---

*End of Part 3. Part 4 (Identity & Authorization Data Flow) follows immediately.*
