# Master Technical Architecture and Implementation Plan
## Part 15 — Admin & Break-Glass

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §17.1 (Bank Admin Portal internal
least-privilege) and §17.2 (Platform Admin Console break-glass). Builds on Part 1 §2.43/§2.44.

---

## A. Bank Admin — Sensitive Operation Flow

```
Bank Admin (authenticated, tenant-scoped)
     │
     ▼
[1] Permission check ──────── Authorization Engine: does this admin's role hold the specific
     │                        permission for the requested action (e.g. "connector.write",
     │                        "confidence_policy.write", "security_policy.write")? Bank Admin
     │                        Portal has NO single flat "admin" role — permissions are granted
     │                        per functional area (architecture §17.1).
     │
     ├── DENIED ──▶ 403, audited
     │
     ▼ (permitted)
[2] Sensitivity classification ── Is this action on the sensitive list? (security policy changes,
     │                        revoking another admin's access, lowering a retention period,
     │                        disabling ABAC, changing confidence policy below a value that
     │                        would still respect the platform floor but represents a material
     │                        loosening)
     │
     ├── NOT sensitive ──▶ [4] Execute directly
     │
     ▼ (sensitive)
[3] Second approval ─────────── The change is written as a `configuration_versions` row in
     │                        status="draft" (Part 5 §11) and a second admin — distinct from the
     │                        one who proposed it — must approve before it can transition to
     │                        "active". The proposing admin cannot self-approve (same
     │                        requester-id/approver-id check pattern as human escalation,
     │                        Part 5 §8's `approvals` constraint, applied here to admin actions).
     │
     ▼ (approved by a second admin)
[4] Execute ──────────────────── Configuration Service (Part 1 §2.30) activates the new version.
     ▼
[5] Audit ────────────────────── AuditEvent written: actor, action, before/after values
     (PII-tokenized where relevant), and — for sensitive actions — both the proposing and
     approving admin's identities.
```

### Bank Admin Portal permission areas (independently grantable, per architecture §17.1)

`identity_config.write`, `connector.write`, `security_policy.write`, `users_roles.write`,
`data_retention.write`, `audit.read`, `custom_request.submit`. A bank may grant one staff member
`connector.write` without `security_policy.write`, and vice versa — this is enforced by the same
RBAC mechanism as every other role in the platform (Part 1 §2.6), scoped to the Bank Admin
Portal's own functional areas as the resource type.

### APIs

```
POST /admin/{tenant_id}/connectors/{id}/draft
POST /admin/{tenant_id}/connectors/{id}/versions/{version}/approve   (second-admin only, rejects
                                                                       if approver == proposer)
POST /admin/{tenant_id}/connectors/{id}/versions/{version}/activate
POST /admin/{tenant_id}/security-policy/draft
POST /admin/{tenant_id}/security-policy/versions/{version}/approve
GET  /admin/{tenant_id}/audit/events
```

---

## B. Platform Operator — Break-Glass Flow

```
Platform Operator (authenticated, platform-level identity — NOT tenant-scoped)
     │
     ▼
[1] Default state ────────────── Zero access to any tenant's business data. Support/Break-glass
     │                          Tooling (Part 1 §2.44) shows only redacted, ticket-scoped views
     │                          by default — no request needed for this baseline level.
     ▼
[2] Break-glass request ──────── POST /platform-admin/break-glass/request
     │                          { tenant_id, reason, scope (e.g. "conversation:{id}",
     │                            "customer_account:{id}"), requested_duration_minutes }
     │
     ▼
[3] Operator eligibility check ── Authorization Engine: does this Platform Operator's OWN role
     │                          permit initiating break-glass requests at all? (Part 1 §2.42's
     │                          internal least-privilege rule — not every platform staff member
     │                          may even request this.)
     │
     ├── DENIED ──▶ 403, audited
     │
     ▼ (eligible)
[4] Tenant consent ─────────────── The request is routed to the tenant's own Bank Admin Portal
     │                          as a pending consent item. The tenant's admin sees: requesting
     │                          operator identity, reason, scope, requested duration.
     │
     ├── Tenant DENIES ──▶ Request closed, no access granted, audited on both sides
     │
     ▼ (tenant APPROVES)
[5] Time-limited grant ─────────── A `break_glass_grants` record is created: grantee, tenant_id,
     │                          scope, granted_at, expires_at (hard ceiling — the shorter of
     │                          the requested duration and a platform-maximum, e.g. 4 hours,
     │                          non-negotiable per grant, renewable only via a new request).
     ▼
[6] Monitored session ─────────── Every action taken under this grant is tagged with the grant's
     │                          ID and logged with heightened detail (not just "read this
     │                          conversation" but the full content accessed, since this IS the
     │                          exceptional path where that level of audit detail is warranted).
     │                          Visible in real time to the tenant's own admin portal — not
     │                          disclosed only after the fact.
     ▼
[7] Expiry ─────────────────────── At `expires_at`, the grant is automatically revoked — no
     │                          action required from either party; the Authorization Engine
     │                          denies any further access under this grant ID from this moment.
     ▼
[8] Audit ──────────────────────── The complete grant lifecycle (request, consent, every action
                                  taken, expiry) is written to the Audit Service, and a summary
                                  is surfaced permanently in the tenant's own audit log — a bank
                                  can always see every break-glass access that has ever occurred
                                  against its data, not only during the grant's active window.
```

### APIs

```
POST /platform-admin/break-glass/request
GET  /platform-admin/break-glass/{id}/status
POST /tenant-admin/{tenant_id}/break-glass/{id}/consent      (tenant admin only — approve/deny)
GET  /tenant-admin/{tenant_id}/break-glass                    (tenant's own visibility into all
                                                                grants against their data, past
                                                                and present)
```

### Authorization rules, stated explicitly

1. A Platform Operator role does not, by itself, confer any tenant data access — access requires
   an active, tenant-consented, unexpired grant (Part 1 §2.44, restated here as the concrete flow).
2. A break-glass request without tenant consent is **denied**, not queued indefinitely.
3. A grant's `expires_at` is enforced by the Authorization Engine on every request during the
   grant's lifetime, not merely checked at grant-creation time — an action attempted one second
   past expiry is denied exactly as if no grant had ever existed.
4. Support engineers (a distinct role from Platform Operator, though the break-glass mechanism is
   shared) follow the identical flow when their redacted default view is insufficient for a given
   ticket (architecture §17.2's support/ops safe-debugging resolution).

---

*End of Part 15. Part 16 (Configuration & Governance) follows immediately.*
