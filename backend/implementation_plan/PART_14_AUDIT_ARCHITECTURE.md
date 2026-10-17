# Master Technical Architecture and Implementation Plan
## Part 14 — Audit Architecture

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §14 (auditability) and the resolved decision at
§23.3 ("audit tamper-resistance"). Builds on Part 1 §2.27 and Part 5 §9.

---

## Architecture Decision vs. Implementation Choice — Stated Up Front

> **Architecture decision (fixed, not swappable): hash-chained, append-only audit records, with no
> delete capability for any role.**

> **Implementation choice (swappable behind the schema below): which physical storage backend
> holds the chain** — a relational table with application-enforced append-only semantics, a
> dedicated object-lock/WORM-capable object store, or a specialized immutable-ledger service. The
> schema and verification logic in this part are written to be backend-agnostic; Part 25 records
> this specific choice as one still requiring a decision (an implementation choice, not an
> architecture gap).

---

## 1. Audit Schema

```python
class AuditEvent(BaseModel):
    id: str
    tenant_id: str
    event_type: str              # e.g. "authentication.success", "retrieval.executed",
                                  # "tool_call.denied", "escalation.approved",
                                  # "configuration.changed", "audit.export"
    actor_user_id: str | None    # null for service-originated events
    resource_type: str | None
    resource_id: str | None
    decision: str | None         # "allow" / "deny" / "approve" / "reject", where applicable
    metadata: dict[str, Any]     # PII-tokenized (Part 13) before this object is ever constructed
    created_at: datetime
    previous_hash: str           # hash of the immediately preceding event IN THIS TENANT'S CHAIN
    current_hash: str            # sha256(previous_hash + canonical_json(this event's own fields
                                  # excluding current_hash))
```

## 2. Chain Structure and Hash Calculation

- **One chain per tenant.** `previous_hash` links to the prior `AuditEvent` for the same
  `tenant_id`, not a single global chain — this keeps per-tenant export and verification
  independent (a bank can verify its own chain without needing access to any other tenant's
  events), consistent with tenant isolation (Part 3 Boundary 4).
- **Hash computation.** `current_hash = SHA256(previous_hash || canonical_json(event_fields))`,
  where `canonical_json` is a deterministic serialization (sorted keys, fixed encoding) so the
  same logical event always hashes identically regardless of implementation-level field ordering.
- **Genesis event.** The first event in a tenant's chain uses a well-known constant (e.g.
  `"0" * 64`) as `previous_hash`.
- **Write path.** The Audit Service (Part 1 §2.27) is the **only** writer to `audit_events` for
  any tenant; it serializes writes per-tenant (a lock or a single-writer queue per `tenant_id`) so
  two concurrent events cannot race and produce two candidate `previous_hash` values for the same
  position in the chain.

## 3. Verification and Tamper Detection

```python
def verify_chain(tenant_id: str, from_event_id: str | None = None) -> VerificationResult:
    events = fetch_ordered(tenant_id, from_event_id)
    expected_previous = events[0].previous_hash if from_event_id else GENESIS_HASH
    for event in events:
        if event.previous_hash != expected_previous:
            return VerificationResult(valid=False, broken_at=event.id)
        recomputed = sha256(event.previous_hash + canonical_json(event, exclude={"current_hash"}))
        if recomputed != event.current_hash:
            return VerificationResult(valid=False, broken_at=event.id)
        expected_previous = event.current_hash
    return VerificationResult(valid=True)
```

- **Any alteration of a past event's fields** changes its `current_hash`, which no longer matches
  what the next event's `previous_hash` recorded — the break is detectable at exactly the altered
  event, not merely "somewhere in the chain."
- **Periodic external anchoring (optional hardening, tenant/deployment-tier configurable).** A
  checkpoint hash (the `current_hash` of the most recent event at a scheduled interval) can be
  published to an external, independent system (a separate storage account, a public timestamping
  service, or a customer-controlled anchor for the most stringent Enterprise/on-prem deployments)
  so that tampering with the database itself — including by someone with database-admin access —
  cannot silently rewrite history without also needing to forge that external anchor.

## 4. Storage Backend Requirements (Whatever Implementation Choice Is Made)

- **No `UPDATE` or `DELETE` grant exists on the audit table/store for any application role**,
  including platform-operator-equivalent roles (architecture §14.3) — enforced at the database
  permission level (a Postgres role with `INSERT`/`SELECT` only, if a relational backend is
  chosen), not merely by application code discipline.
- **Writes are transactional and ordered per tenant** (§2's serialization requirement) regardless
  of backend.
- **Reads are permission-scoped** — access to `GET /audit/events` is itself checked by the
  Authorization Engine against the requester's own role/tenant, same as any other resource
  (architecture §14.3's "read access to audit records is itself permission-scoped").

## 5. Tenant Scope

Every `AuditEvent` carries `tenant_id`; the chain itself is per-tenant (§2). A platform-wide
security-operations view (Part 1 §2.40's cross-tenant observability) aggregates *metrics derived
from* audit events across tenants (counts, anomaly signals) without ever exposing one tenant's raw
audit content to platform staff outside a break-glass grant (Part 1 §2.44).

## 6. Retention and Legal Hold

- `audit_events` retention follows the tenant's configured policy for the `audit` data class
  (Part 5 §10), with a **regulatory-minimum floor** the tenant cannot configure below (a Business
  Decision Required per jurisdiction — Part 25 — since minimum audit retention periods are often
  set by banking regulation, not platform preference).
- Deletion past the retention window (where legally permitted at all — many jurisdictions require
  audit records be retained far longer than most other data classes) checks `legal_holds` first,
  identical to every other data class (architecture §12.6).
- Deletion, when it does occur, is itself logged as its own `AuditEvent` before the underlying
  records are removed — the fact that a class of audit history was purged, and under what
  authority, remains discoverable even after the underlying detail is gone.

## 7. Backup

Audit records are included in the tenant-scoped backup regime (Part 19) with the same RPO/RTO
targets as the rest of the tenant's data — an audit trail that is lost in a disaster is itself a
compliance and security failure, not merely an operational inconvenience.

## 8. Export

`GET /audit/export?tenant_id=&from=&to=` produces a portable, independently-verifiable export
(the raw events plus enough of the chain to run §3's verification against the exported slice) —
this is the mechanism behind the compliance evidence packages described in architecture §17.2 and
the tenant-offboarding audit export requirement (architecture §23.3). Export access is itself an
audited event.

---

*End of Part 14. Part 15 (Admin & Break-Glass) follows immediately.*
