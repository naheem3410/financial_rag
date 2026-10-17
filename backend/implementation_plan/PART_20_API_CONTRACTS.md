# Master Technical Architecture and Implementation Plan
## Part 20 — API Contracts

Foundational platform APIs only, per the explicit instruction — module-specific endpoints (Credit
Decision Support's own query shape, KYC Guidance's own flow) are deferred to each module's own
specification when that module is scoped for build (Part 21's MVP module set names which modules
that applies to first).

Every API below inherits, without restating per-endpoint: TLS in transit; request/response schema
validation (rejecting anything non-conforming before it reaches business logic); rate limiting per
Part 17's rate-limit-abuse control; and an audit event written for every call, success or failure,
per Part 14.

---

## Authentication & Session

### `POST /auth/customer/flow/{tenant_id}/start`
- **Request.** `{ flow_step_type: "collect_input", initial_input: {...} }`
- **Response.** `{ flow_id, next_step: {...} }`
- **Authentication.** None yet — this endpoint initiates authentication.
- **Authorization.** None.
- **Tenant requirement.** `tenant_id` in path, validated against an active tenant.
- **Idempotency.** Not applicable — each call advances flow state.
- **Errors.** `404` unknown tenant, `429` rate-limited (Part 6 §3's multi-dimensional keying).
- **Audit event.** `auth.customer_flow.started`.

### `POST /auth/customer/flow/{flow_id}/step`
- **Request.** `{ step_data: {...} }` (PII-tokenized before this object is logged anywhere)
- **Response.** `{ next_step: {...} }` or, on completion, `{ access_token, refresh_token }`
- **Authentication.** Flow-session token (issued at flow start, distinct from a platform session
  token).
- **Authorization.** None beyond flow-session validity.
- **Tenant requirement.** Inherited from `flow_id`.
- **Idempotency.** Retrying an identical step submission is safe (no side effect beyond
  re-evaluating the same input); repeated OTP submission is rate-limited per Part 6 §3.
- **Errors.** `400` invalid step data, `423` locked (rate-limit lockout), `408` flow timeout.
- **Audit event.** `auth.customer_flow.step_completed` / `auth.customer_flow.failed`.

### `POST /auth/federate/{provider}`
- **Request.** Provider-specific (SAML assertion / OIDC authorization code).
- **Response.** `{ access_token, refresh_token }`
- **Authentication.** The IdP's own signed assertion.
- **Authorization.** None — produces identity, not permission.
- **Tenant requirement.** Resolved from the IdP configuration matched to the assertion's issuer.
- **Idempotency.** N/A.
- **Errors.** `401` invalid/expired assertion.
- **Audit event.** `auth.federation.success` / `auth.federation.failed`.

### `POST /auth/token/refresh`
- **Request.** `{ refresh_token }`
- **Response.** `{ access_token }`
- **Authentication.** The refresh token itself.
- **Authorization.** None.
- **Tenant requirement.** Inherited from the token.
- **Idempotency.** Each call issues a new access token; safe to retry.
- **Errors.** `401` expired/revoked refresh token.
- **Audit event.** `auth.token.refreshed`.

### `POST /auth/session/revoke`
- **Request.** `{ session_id }`
- **Response.** `204`
- **Authentication.** Admin session (self-revocation) or Bank Admin with `users_roles.write`.
- **Authorization.** Object-level — an admin may revoke only sessions within their own tenant.
- **Tenant requirement.** Enforced.
- **Idempotency.** Safe to call repeatedly on an already-revoked session.
- **Errors.** `403` cross-tenant attempt.
- **Audit event.** `auth.session.revoked`.

---

## Conversation & RAG Query

### `POST /conversations`
- **Request.** `{ module_id }`
- **Response.** `{ conversation_id }`
- **Authentication.** Platform session token.
- **Authorization.** Module access check (does this role/tenant have this module enabled per
  `feature_entitlements`, Part 5 §12).
- **Tenant requirement.** From `SecurityContext`.
- **Idempotency.** N/A — each call creates a new conversation.
- **Errors.** `403` module not entitled.
- **Audit event.** `conversation.created`.

### `POST /conversations/{id}/messages`
- **Request.** `{ text }`
- **Response.** `{ message_id, answer_text, citations, confidence_score,
  verification_trace_summary }` or `{ message_id, status: "escalated", escalation_id }`
- **Authentication.** Platform session token.
- **Authorization.** Object-level — `conversations.id` must belong to the requester (Part 7 §4).
- **Tenant requirement.** Enforced via the conversation's own tenant scope.
- **Idempotency.** A client-supplied `request_id` header allows safe retry of a message submission
  without double-processing (distinct from a *new* message, which is always a new logical
  request).
- **Errors.** `403` not conversation owner (and no valid privileged grant), `503` retrieval/LLM
  unavailable → returns an abstention response body, not a raw 503, per Principle 5.
- **Audit event.** The full Part 8 pipeline's audit record (query, evidence, model/prompt
  versions, confidence, decision).

### `GET /conversations/{id}/messages`
- **Request.** Query params: pagination.
- **Response.** `{ messages: [...] }` (PII-tokenized, resolved only for display to the authorized
  owner where the frontend is entitled to show detokenized values it has independent right to —
  see Part 13 for exactly which values remain tokenized even to the owning user, e.g. an internal
  system reference the user never needed to see raw).
- **Authentication.** Platform session token.
- **Authorization.** Object-level (Part 7 §4).
- **Tenant requirement.** Enforced.
- **Idempotency.** N/A — read-only.
- **Errors.** `403`/`404` (indistinguishable, per Part 7 §4 step 6's information-leakage avoidance).
- **Audit event.** `conversation.read` (elevated detail if accessed under a privileged grant,
  Part 7 §9).

### `DELETE /conversations/{id}`
- **Request.** None.
- **Response.** `202 Accepted` (deletion is a cascade job, not synchronous).
- **Authentication.** Platform session token.
- **Authorization.** Object-level, owner only (or an admin with an explicit deletion permission).
- **Tenant requirement.** Enforced.
- **Idempotency.** Safe to call repeatedly.
- **Errors.** `409` if an active legal hold blocks deletion (Part 7 §6).
- **Audit event.** `conversation.deletion_requested` / `conversation.deletion_blocked_by_hold`.

---

## Tool / Escalation

### `POST /escalations/{id}/approve`
### `POST /escalations/{id}/reject`
- **Request.** `{ reasoning }`
- **Response.** `204`
- **Authentication.** Platform session token, reviewer role.
- **Authorization.** Reviewer eligibility + self-approval rejection check (Part 5 §8,
  `approvals` constraint) — `403` if `reviewer_id == requester_id` or reviewer is the action's
  subject.
- **Tenant requirement.** Enforced.
- **Idempotency.** A second approve/reject call on an already-decided escalation returns `409`,
  never silently overwrites the first decision.
- **Errors.** `403` self-approval attempt, `409` already decided, `410` timed out (moved to
  `timed_out` state, no longer approvable — Part 5 §8).
- **Audit event.** `escalation.approved` / `escalation.rejected`.

---

## Admin (Bank-facing, foundational subset)

### `POST /admin/{tenant_id}/connectors`
- **Request.** `ConnectorProfile` (Part 10 §1), status forced to `draft`.
- **Response.** `{ connector_id, version }`
- **Authentication.** Bank Admin session.
- **Authorization.** `connector.write` permission (Part 15 §A).
- **Tenant requirement.** Path `tenant_id` must match session's tenant.
- **Idempotency.** N/A — creates a new draft.
- **Errors.** `400` schema-invalid profile (unregistered auth/encryption plugin, Part 10 §2).
- **Audit event.** `connector.draft_created`.

### `POST /admin/{tenant_id}/connectors/{id}/test`
- **Request.** None (uses the draft's own configuration).
- **Response.** `{ status: "success"|"failed", details }`
- **Authentication.** Bank Admin session.
- **Authorization.** `connector.write`.
- **Tenant requirement.** Enforced.
- **Idempotency.** Safe to call repeatedly.
- **Errors.** N/A — failure is a normal response body, not an HTTP error.
- **Audit event.** `connector.tested`.

### `POST /admin/{tenant_id}/connectors/{id}/versions/{version}/activate`
- **Request.** None.
- **Response.** `204`
- **Authentication.** Bank Admin session.
- **Authorization.** `connector.write` + second-approver check if the connector is classified
  sensitive (write-enabled connectors, per Part 15 §A).
- **Tenant requirement.** Enforced.
- **Idempotency.** Activating an already-active version is a no-op, `200` not `409`.
- **Errors.** `409` if the draft has not passed `test` first.
- **Audit event.** `connector.activated` (with proposer + approver identities if second-approval
  was required).

---

## Licensing (Platform Admin-facing, foundational subset)

### `POST /licenses/issue`
- **Request.** `{ tenant_id, plan_id, custom_entitlements: {...} }`
- **Response.** `{ license_id, signed_entitlement_document }`
- **Authentication.** Platform Admin session.
- **Authorization.** Platform Admin Console only.
- **Tenant requirement.** N/A — this is a platform-level operation targeting a tenant.
- **Idempotency.** Each call issues a new license version; re-issuing supersedes the prior one.
- **Errors.** `400` invalid plan/entitlement combination.
- **Audit event.** `license.issued`.

### `GET /licenses/{tenant_id}/verify`
- **Request.** None.
- **Response.** `{ valid, expires_at, entitlements: {...} }`
- **Authentication.** Service-internal (called by the platform's own components to check
  entitlement, e.g. before enabling a module) or Bank Admin (to view their own entitlement).
- **Authorization.** A tenant may verify only its own license.
- **Tenant requirement.** Enforced.
- **Idempotency.** N/A — read-only.
- **Errors.** `410` expired.
- **Audit event.** Not separately audited for internal service-to-service calls (would be
  excessive volume); audited when called by a human via the Bank Admin Portal.

---

*End of Part 20. Part 21 (Module Architecture) follows immediately.*
