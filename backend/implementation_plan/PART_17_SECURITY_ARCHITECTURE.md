# Master Technical Architecture and Implementation Plan
## Part 17 — Security Architecture

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §8 in full, consolidating controls already
specified in earlier parts and adding the remaining implementation-level controls (SSRF, SQLi,
session hijacking, replay, rate-limit abuse) not yet made explicit.

---

## Control-by-Control

### XSS

- **Control.** Output encoding on every rendered value in the Web Applications (Part 1 §2.1);
  Content-Security-Policy headers restricting script sources; no `dangerouslySetInnerHTML`-style
  raw HTML rendering of any LLM-generated or user-generated content anywhere in the frontend.
- **Enforcement point.** Frontend build pipeline (CSP header configuration) + component-level
  encoding discipline, verified by an automated scan in CI (Part 23).

### CSRF

- **Control.** CSRF tokens on every state-changing request (POST/PATCH/DELETE), validated
  server-side at the API Gateway before the request reaches any backend service.
- **Enforcement point.** API Gateway (Part 1 §2.2).

### SSRF

- **Control.** The Connector Framework (Part 1 §2.24) never accepts an arbitrary, user- or
  LLM-supplied URL as a call target. Every connector's `base_endpoint` (Part 10 §1.1) is a
  pre-registered, tenant-admin-configured value, set through the Bank Admin Portal's onboarding
  wizard — never derived at request time from LLM output or user input. There is no
  "fetch this URL" tool exposed to any LLM call site or agent tool registry (verified against the
  tool registry, Part 5 §5 — no such tool exists in the platform's design). This closes the
  specific SSRF vector where an agent could otherwise be tricked into causing the platform's own
  infrastructure to make a request to an internal or attacker-controlled address.
- **Enforcement point.** Tool/MCP Gateway (Part 1 §2.23) — `arguments` are validated against the
  tool's declared schema, which for every connector-backed tool never includes an arbitrary
  target-URL field.

### IDOR / BOLA

- **Control.** Object-level authorization at every data-access and tool-call boundary (Part 4 §4,
  Part 7 §4, Part 10 §3) — every reference to a specific record (account, conversation, employee
  record, escalation) is checked against the requester's `SecurityContext` before access is
  granted, never trusted from the request path alone.
- **Enforcement point.** Authorization Engine (Part 1 §2.6), invoked at Conversation Service,
  Retrieval, and Tool/MCP Gateway.

### SQL Injection

- **Control.** Parameterized queries exclusively, platform-wide — no raw string concatenation into
  SQL in any service (Part 3, Boundary 5). Enforced by a static-analysis rule in CI that flags any
  string-built query construction as a build failure, not merely a code-review convention.
- **Enforcement point.** CI pipeline (SAST, Part 17 §Security Operations below) + ORM/query-builder
  usage discipline in every service touching the Relational Database.

### Authentication Attacks (credential stuffing, brute force)

- **Control.** Rate limiting on every authentication endpoint, keyed on tenant + identity + device
  + IP + session + endpoint together (architecture §6.3), not IP alone; lockout after a
  configurable threshold of failed attempts; MFA available and tenant-configurable to require.
- **Enforcement point.** Authentication Service (Part 1 §2.5) and Customer Authentication Flow
  Engine (Part 1 §2.4).

### Authorization Bypass

- **Control.** No service implements ad hoc authorization logic (Part 1 §3 cross-component rule
  1) — every decision routes through the Authorization Engine, which fails closed on any
  ambiguity or unreachability (Part 1 §2.6). Frontend-layer checks are defense-in-depth only,
  never authoritative (Part 1 §2.1) — server-side re-verification is mandatory on every request.
- **Enforcement point.** Authorization Engine, called synchronously by every other service.

### Prompt Injection

- **Control.** Retrieved documents, conversation history, and tool/connector responses are all
  structurally treated as data, never instructions (architecture §8.4, enforced via the Context
  Builder's constrained input signature, Part 1 §2.10, and response-schema validation at the
  Tool/MCP Gateway, Part 1 §2.23). Input-side content filtering plus embedding-based anomaly
  detection runs in parallel with retrieval (architecture §8.3).
- **Enforcement point.** Context Builder, Tool/MCP Gateway, input anomaly detection (a component
  not separately named in Part 1 — implemented as a filter stage inside the API Gateway's request
  pipeline, running asynchronously alongside, not serially before, the main request path).

### PII Leakage

- **Control.** Full PII architecture (Part 13) — detection and tokenization before any downstream
  component, reversible resolution restricted to the Connector Framework's own request
  construction only.
- **Enforcement point.** PII/Tokenization Service (Part 1 §2.28), invoked at every ingress point
  (Part 13 §5's boundary table).

### Credential Leakage

- **Control.** Every secret is a reference, never a value, outside Secrets Management (Part 1
  §2.29); redaction before logging/tracing/prompting applies to credentials the same way it
  applies to PII (they are, functionally, a category of sensitive data subject to the identical
  rule).
- **Enforcement point.** Secrets Management, plus the same redaction-before-export discipline as
  Part 1 §2.40 (Observability).

### MCP Compromise

- **Control.** Per-tenant MCP server instances (Part 1 §2.4/§10.11) contain a compromise to one
  tenant; every tool response is schema-validated before being trusted (Part 10 §3).
- **Enforcement point.** Tool/MCP Gateway.

### Agent Loops

- **Control.** The execution-budget object (Part 9 §1) — max tool calls, runtime, cost, recursion,
  retries — enforced in Agent Orchestrator code, never left to prompt instruction (Part 9 §2).
- **Enforcement point.** Agent Orchestrator (Part 1 §2.22), plus a background watchdog process
  independent of the orchestrator's own loop (so a genuinely stuck orchestrator process cannot
  survive by never reaching its own check).

### Supply-Chain Attacks

- **Control.** Exact dependency pinning platform-wide; an SBOM generated per release; automated
  vulnerability scanning of dependencies and container images before every release (architecture
  §8.10). A compromised dependency is analyzed for blast radius using the same reasoning as a
  compromised connector — no single package has a path to secrets or cross-tenant data by design,
  since secrets are never held in application memory longer than a single resolution call
  (Part 1 §2.29) and no third-party library is granted direct database or Secrets Management
  credentials of its own.
- **Enforcement point.** CI/CD pipeline (dependency scanning, SBOM generation), Security
  Operations program (§below).

### Malicious Uploads

- **Control.** Full ingestion hardening (Part 12 §1–§5) — malware scanning, MIME validation,
  macro rejection/stripping, size limits, quarantine on ambiguous results.
- **Enforcement point.** Malware Scanning (Part 1 §2.33), Document Ingestion (Part 1 §2.32).

### Session Hijacking

- **Control.** Short-lived access tokens with refresh (Part 1 §2.5); session/device tracking;
  step-up authentication required for sensitive actions regardless of an active session's
  validity (architecture §6.8) — a hijacked session alone is insufficient to move money or access
  the most sensitive record classes; idle-session auto-lock and full state clearing on logout,
  particularly relevant for shared branch terminals (architecture §11.9).
- **Enforcement point.** Authentication Service, Web Applications (client-side session hygiene as
  defense-in-depth).

### Replay Attacks

- **Control.** Idempotency keys on every write operation (Part 11 §3) prevent a replayed write
  request from executing twice; short-lived tokens with signature validation prevent a captured
  token from being usable indefinitely; nonce/timestamp validation on any signed request binding
  (Part 10 §1.7's HMAC signing strategy) rejects a request replayed outside its validity window.
- **Enforcement point.** Connector Framework (idempotency), Authentication Service (token
  expiry), the specific auth-strategy plugin in use for signed-request connectors.

### Rate-Limit Abuse

- **Control.** Per-tenant, per-user, per-module, per-connector, and per-API-client concurrency and
  rate limits enforced at the API Gateway (architecture §5.4's resource-isolation decision,
  Part 1 §2.2), sized so no single actor can degrade the platform for others (the noisy-neighbor
  resolution from the architecture review's §23.3).
- **Enforcement point.** API Gateway, backed by the Cache (Part 1 §2.38) for counter storage.

---

## Security Operations Program (architecture §8.9, the standing practice)

- **Automated dependency/container scanning** in CI, on every build.
- **SAST** on every commit (the SQL-injection static-analysis rule above is one instance of this
  broader practice).
- **Periodic third-party penetration testing** — cadence a Business Decision Required (Part 25),
  typically annual or pre-major-release at minimum.
- **SBOM generated per release.**
- **Incident-response runbook**, with defined severity levels and notification SLAs — a Business
  Decision Required for the exact SLA numbers, structurally required regardless.
- **Credential-compromise runbook** — immediate rotation (via Secrets Management, Part 1 §2.29),
  revocation, tenant notification within a committed window.

---

*End of Part 17. Part 18 (Observability) follows immediately.*
