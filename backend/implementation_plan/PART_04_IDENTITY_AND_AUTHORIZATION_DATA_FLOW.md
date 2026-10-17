# Master Technical Architecture and Implementation Plan
## Part 4 — Identity & Authorization Data Flow

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` Principle 3, Principle 4, §6.6, §6.7.

---

## 1. The Canonical Security Context

Every authenticated request in the platform carries exactly one instance of this object, created
once and threaded immutably through the entire request lifecycle:

```python
class SecurityContext(BaseModel):
    tenant_id: str
    user_id: str                       # platform's own user record id, never the raw IAM subject
    session_id: str
    roles: list[str]                   # RBAC roles resolved at authentication time
    abac_attributes: dict[str, Any]    # region, department, clearance, case_assignment, etc.
    pbac_policy_context: dict[str, Any]  # inputs the Policy Engine needs to evaluate policy
    customer_scope: list[str] | None   # for customer sessions: the account/customer ids this
                                        # session is authorized to reference — resolved from the
                                        # bank's own system at authentication, never from user input
    data_classification_clearance: list[str]  # which classification levels this context may read
    module_mode: Literal["controlled", "agentic"]  # inherited from the module, never chosen
    issued_at: datetime
    expires_at: datetime
```

**Immutability rule.** `SecurityContext` is constructed exactly once per request, at the boundary
where authentication completes (API Gateway, having received a validated token from the
Authentication Service). No downstream component is permitted to construct a new
`SecurityContext`, mutate an existing one, or accept a client-supplied one — every function that
needs authorization information receives this object by reference and reads from it, never writes
to it.

## 2. Creation Sequence

```
1. Request arrives at API Gateway with a bearer token.
2. API Gateway calls Authentication Service to validate the token.
3. Authentication Service returns: user_id, tenant_id, session_id (from the session record it
   owns) — this is the ONLY step that touches raw token material.
4. API Gateway calls User/Session Service with (tenant_id, user_id) to fetch role assignments.
5. API Gateway calls Authorization Engine to resolve ABAC attributes and PBAC policy context for
   this user within this tenant.
6. For a customer session specifically: API Gateway calls the Connector Framework (via the bank's
   own CBS/customer-record system) to resolve customer_scope — the list of accounts/records this
   authenticated customer is actually linked to. This is fetched from the BANK'S OWN SYSTEM, never
   inferred from the request, never supplied by the client.
7. API Gateway assembles the SecurityContext object and attaches it to the request as internal
   context (never exposed to or accepted from the client).
8. Every subsequent internal service call carries this SecurityContext unchanged.
```

**Why customer_scope is resolved from the bank's system, not the platform's own database:** the
platform's own records are not the authoritative source of which accounts belong to which
customer (Principle 2) — the bank's CBS/customer-record system is. Caching `customer_scope` for
performance is permitted, but the cache must be invalidated on a defined TTL short enough that a
bank-side change (e.g., an account being closed or reassigned) is reflected promptly, and the
cache key includes `tenant_id` and `user_id` per the same rule as every other cache in the
platform (Part 1 §2.38).

## 3. Propagation

```
API Gateway
    │  SecurityContext attached
    ▼
Conversation Service ──▶ Context Builder ──▶ RAG Orchestration ──▶ Retrieval
    │                         │                     │                  │
    │                         │                     ▼                  ▼
    │                         │              LLM Gateway         Authorization Engine
    │                         │              (SecurityContext    (checks classification
    │                         │               used only to        clearance before any
    │                         │               resolve tenant's    candidate is returned)
    │                         │               own model/key —
    │                         │               NEVER placed into
    │                         │               the prompt content
    │                         │               itself)
    │                         ▼
    │                  Conversation history filtered to
    │                  (tenant_id, user_id, session_id,
    │                   conversation_id, module, scope)
    ▼
Tool/MCP Gateway ──▶ Connector Framework ──▶ Bank System
    │
    ▼
Authorization Engine (object-level check: is the requested
account/record within customer_scope / abac_attributes?)
```

**The SecurityContext itself is never serialized into an LLM prompt.** Where the LLM needs to know
"which entity is this," it receives a placeholder (`<ENT_1>`, `<ACCOUNT_1>`) from the Placeholder
Registry / PII Tokenization Service, never the raw `SecurityContext` fields. This is deliberate:
the LLM never sees `tenant_id`, `user_id`, or `customer_scope` as literal values it could
be tricked into reasoning about or repeating incorrectly.

## 4. The LLM Is Never Authoritative — Enforcement Table

| What the LLM must never determine | Technical enforcement point | Why it's structurally impossible, not just discouraged |
|---|---|---|
| **Tenant** | API Gateway resolves `tenant_id` from the validated token at step 3 of creation, before any LLM call exists in the request lifecycle. The LLM Gateway (Part 1 §2.16) requires `tenant_id` as a non-optional parameter on every call, sourced from the immutable `SecurityContext`, never from LLM output. | The LLM is invoked only *after* tenant is already fixed; there is no code path where an LLM's output flows back into tenant resolution. |
| **User** | Same as tenant — `user_id` is fixed at step 3, before the first LLM call in the request. | Same reasoning. |
| **Account (customer identity)** | `customer_scope` is resolved from the bank's own system at context-creation time (§2, step 6). Any account reference the LLM produces in a tool-call proposal is checked against this pre-resolved list by the Tool/MCP Gateway (Part 1 §2.23) — the LLM's proposal is a request, not a grant. | The check happens in code, comparing the LLM's proposed value against a value the LLM had no part in producing. A proposal outside `customer_scope` is rejected regardless of how convincingly the LLM argues for it. |
| **Role** | Roles are resolved from `user_roles` (Part 5) at context-creation time, from the User/Session Service — a database read, not an LLM inference. | No code path passes LLM output into the `user_roles` table. |
| **Permission** | The Authorization Engine (Part 1 §2.6) evaluates permission from `SecurityContext.roles` / `abac_attributes` against the Policy Engine — the LLM is never a party to this evaluation and has no API surface that could write to `policies` or `user_roles`. | There is no `grant_permission` tool exposed to any LLM call site or agent tool registry (verified against the tool registry in Part 5/Part 10 — no such tool exists in the platform's design). |
| **Customer identity claims made in conversation** | If a user states "I am customer X" or "I am the CEO" in free text, the Context Builder (Part 1 §2.10) never reads conversation content as an identity signal — its inputs are strictly `(tenant_id, user_id, session_id, conversation_id, module, authorization_scope)`, all sourced from `SecurityContext`, never from message text. | Structural: the Context Builder's function signature has no parameter through which a claimed identity from message content could enter. |

## 5. Session Revocation Propagation

When a bank disables a user's IAM account (architecture §6.9), the following sequence closes the
gap between disablement and session invalidation:

```
1. Authentication Service performs a periodic re-validation check against the tenant's IdP for
   every active session (interval configurable per tenant, default short enough that a disabled
   account cannot meaningfully continue operating — this interval is a Business Decision Required,
   see Part 25, since it trades off IdP call volume against exposure window).
2. On detecting the account is disabled, Authentication Service invalidates the session
   immediately (§2.5 of Part 1) — not merely lets it expire naturally.
3. Any in-flight Agent Orchestrator task for that session is signaled to stop at its next
   checkpoint (tool-call boundary) rather than completing — an already-issued tool call in flight
   is allowed to finish (to avoid an inconsistent partial write), but no new tool call is issued
   under a revoked session's SecurityContext.
4. The invalidation event is logged to the Audit Service.
```

---

*End of Part 4. Part 5 (Database Architecture) follows immediately.*
