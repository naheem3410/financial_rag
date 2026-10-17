# Master Technical Architecture and Implementation Plan
## Part 19 — Disaster Recovery & Business Continuity

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §19 (RPO/RTO, external-failure distinction).
Mapped per stateful component, per the explicit instruction not to treat "backup enabled" as a
sufficient answer.

---

## Per-Component RPO/RTO Mapping

| Component | RPO (max data loss) | RTO (max time to restore) | Backup mechanism | Failover mechanism | Verification |
|---|---|---|---|---|---|
| Relational Database | 15 min (default, tenant-configurable) | 4 hours | Continuous WAL/transaction-log shipping to a separate storage location, plus periodic full snapshots | Standby replica promotion (where the deployment topology supports it) or restore-from-snapshot for smaller deployments | Scheduled, automated restore drills against a non-production environment; a restore that cannot be automatically verified to match expected row counts/checksums is treated as a failed drill, not a passed one |
| Vector Database | 15 min (default) | 4 hours | Periodic snapshot of the index; since embeddings are deterministically reproducible from `propositions` (Part 6), a full rebuild-from-source is also a valid recovery path where snapshot recency is insufficient, at the cost of rebuild time rather than data loss | Snapshot restore, or rebuild-from-propositions if the snapshot is stale beyond RPO | Post-restore, the three-index invariant check (Part 6 §6) is re-run before the restored index is considered production-ready — a restore that fails this check is not complete |
| Object Storage | Near-zero (most object storage backends offer built-in versioning/replication) | 4 hours | Versioned storage with cross-region or cross-zone replication, per deployment tier | Automatic failover to the replicated copy | Periodic integrity check (checksum verification) on a sample of stored objects |
| Cache | N/A — not backed up | Immediate (cache is rebuilt on demand) | None required | A cache-miss on failover is a performance degradation, never a correctness issue, since cache is never the sole source of truth for anything (Part 1 §2.38) | N/A |
| Queue / Event Bus | Near-zero for in-flight messages (durable queue with acknowledgment) | 1 hour | Durable message persistence with replication, per the underlying queue technology's own guarantees | Consumer reconnect to a replicated broker instance | Dead-letter queue depth is monitored; a growing dead-letter queue post-failover is itself an alert condition |
| Secrets Management | Near-zero | 1 hour | Secrets backend's own backup/replication (implementation-choice-dependent) | Failover to a replicated secrets-manager instance | Periodic access-verification check (can a known-good reference still be resolved) |
| PII Vault | Matches its own configured retention window — losing vault entries older than that window is not a DR concern since they'd be deleted anyway; entries within the retention window follow the same RPO as the Relational/Secrets backing store they're persisted in | 4 hours | Same mechanism as its backing encrypted store | Same as backing store | Same as backing store |
| Audit Service store | Near-zero — an audit gap is itself a compliance concern, not merely an operational one | 1 hour (higher priority than general database restore, given Part 14 §7's requirement that audit loss is a compliance failure) | Same mechanism as Relational Database, with a shorter acceptable RPO reflecting the higher stakes | Same as Relational Database | Chain verification (Part 14 §3) is re-run on the restored audit data before it is considered trustworthy — a restored audit log that fails hash-chain verification is treated as compromised, not merely incomplete, and triggers the incident-response runbook (Part 17) |
| License/Plan data | Near-zero | 1 hour | Same as Relational Database | Same as Relational Database | Signed-entitlement verification (architecture §22.2) re-run post-restore |

## External Dependencies — Explicitly Not Treated as Disaster States (architecture §19.2)

| Dependency | Behavior on failure |
|---|---|
| LLM provider (bank's own key/endpoint) | Graceful degradation — the Fixed pipeline abstains ("temporarily unavailable") rather than treating this as a platform-wide incident. Not covered by the platform's own RPO/RTO commitments, since it is not the platform's infrastructure. |
| Bank CBS / Middleware | Same graceful-degradation behavior (Part 1 §2.24's circuit-breaker/backoff, Part 3 Boundary 10). |
| Bank's IAM / IdP | Authentication fails safely (no fallback to an unauthenticated session, Part 1 §2.3) — new logins are blocked until the IdP recovers; existing sessions continue per their own expiry until the next periodic re-validation check (Part 4 §5). |
| Bank's own Secrets Manager (on-prem BYOK deployments) | The platform's own components fail closed on any secret-resolution failure (Part 1 §2.29) — no default or cached fallback credential is ever used. |

**Why this distinction matters operationally:** a bank's CBS outage should never trigger the
platform's own disaster-recovery runbook, incident paging, or RPO/RTO reporting — it is the bank's
outage, handled by graceful degradation on the platform's side, not a platform incident.

### The Audit Service Store's 1-Hour RTO — Default: Separate Infrastructure from Phase 0

The table above gives the Audit Service store a tighter RTO (1 hour) than the general Relational
Database (4 hours), reflecting that an audit gap is a compliance failure, not merely an
operational one. **This tighter figure is only achievable if `audit_events` is provisioned on
physically separable infrastructure from the rest of the relational tables** — a relational table
sharing the surrounding cluster's own restore process cannot recover faster than that cluster
does, regardless of what number is written in a table.

**Default, effective from Phase 0: `audit_events` is provisioned on infrastructure physically
separate from the general Relational Database from the very first commit, not retrofitted
later.** This is a deliberate exception to Part 25's usual "engineers choose the specific backend
freely" latitude, for a reason specific to this one component: the hash chain (Part 14 §2–§3)
is only as trustworthy as the assumption that no re-serialization, no write-ordering gap, and no
migration event ever touches it. Migrating a live, hash-chained log to new infrastructure after it
already holds real history risks three concrete failures — a different environment's canonical
JSON serialization producing different hashes for identical historical events (making the entire
prior chain fail its own verification with no way to distinguish that from real tampering), a
write-ordering fork if writes continue against both the old and new store during cutover (the
exact signature the chain exists to detect as tampering), and the compliance optics of ever having
to tell an auditor "we moved this once, mid-operation." None of these risks exist if the correct
infrastructure is chosen once, before any real audit history accumulates.

**Deployment-specific exception, not a silent override.** A bank choosing on-premises deployment
may explicitly opt to co-locate the audit store with the general database on their own
infrastructure, accepting the general database's RTO for their audit trail as a documented
trade-off they own — this is their infrastructure and their call to make, but it must be an
explicit choice recorded in that tenant's deployment configuration, never the platform's silent
default for that deployment type.

---

## Tenant-Independent Restore

**Backups are tenant-partitioned, never a single combined snapshot restored wholesale**
(architecture §19.3). Concretely:

- Relational Database backups are taken and restorable at a granularity that permits restoring
  one tenant's rows (via the same Row-Level Security tenant partitioning that governs live
  access, Part 3 Boundary 4) without touching another tenant's data in the same restore
  operation.
- Vector Database and Object Storage backups are already physically/logically separated by
  tenant namespace/prefix (Part 1 §2.35/§2.37), so a restore naturally scopes to one tenant's
  partition.
- **A restore operation is required to specify a `tenant_id`** — there is no "restore everything"
  operation exposed in the platform's recovery tooling that could accidentally overwrite or
  affect a tenant not intended to be restored.

## Recovery Testing

- **Scheduled, automated restore drills** (not merely "backups exist and are assumed to work") —
  run against a non-production environment on a defined cadence (a Business Decision Required for
  exact frequency, Part 25), exercising the full backup → restore → verification sequence per
  component above.
- A drill that fails **any** component's verification step (three-index invariant, audit
  chain-verification, license-signature verification) is treated as a failed drill requiring
  root-cause investigation before the next scheduled drill — not silently retried until it passes.
- **On-prem deployments** — recovery testing responsibility is shared: the platform vendor
  provides the tooling and the verification logic (the same checks as above, packaged into the
  deployment artifact per architecture §22.10), but the bank's own infrastructure team executes
  the drill against their own environment, since the vendor does not operate that infrastructure.

---

*End of Part 19. Part 20 (API Contracts) follows immediately.*
