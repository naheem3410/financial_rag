# Master Technical Architecture and Implementation Plan
## Part 5 — Database Architecture

Implements the relational data model backing every service in Part 1. Postgres/JSONB is assumed
consistent with the existing research codebase's `offline/indexing/metadata_store_postgres.py`
convention — the specific engine remains an **[implementation choice]** behind the `MetadataStore`
interface (architecture §5.0); the schema below is engine-agnostic in its column-level design.

**Conventions applied to every table below:**
- Every table carries `tenant_id` (except the two explicitly platform-global tables noted) and a
  Row-Level Security policy keyed to it (architecture §4.4).
- Every table carries `created_at`, `updated_at`. Tables holding anything security-relevant also
  carry `created_by`.
- No table has a hard `DELETE` capability exposed to any application role for records governed by
  a retention policy (§12.5 of the architecture document) — deletion is a state transition
  (`deleted_at` set, or a move to a tombstone table) that respects legal holds (§12.6), never a
  destructive `DELETE` issued from an API handler.

---

## 1. Tenancy & Identity

### `tenants`
| Field | Notes |
|---|---|
| `id` (PK) | |
| `name`, `status` (`active`/`suspended`/`offboarding`) | |
| `deployment_type` (`saas`/`dedicated`/`on_prem`) | |
| `plan_id` (FK → `plans`) | |
| `idp_config` (JSONB) | Connection metadata only — client ID, endpoint, claim-mapping rules. **No client secret stored here** — secret lives in Secrets Management, referenced by `idp_config.secret_reference`. |
| **Tenant ownership.** N/A — this table *is* the tenant boundary's root. | |
| **Authorization.** Write: Platform Admin Console only. Read: a tenant may read only its own row. | |
| **Retention.** Retained through offboarding for audit-export purposes (architecture §23.3), then tombstoned per contractual data-destruction terms. | |

### `users`
| Field | Notes |
|---|---|
| `id` (PK), `tenant_id` (FK) | |
| `identity_provider_subject` | The IAM's own subject identifier — **not** the person's raw name/email if avoidable; kept as an opaque mapping key. |
| `user_type` (`employee`/`customer`/`bank_admin`/`platform_operator`) | |
| `status` (`active`/`disabled`) | Set to `disabled` on IAM-side revocation detection (Part 4 §5). |
| **Sensitive fields.** `identity_provider_subject` — encrypted at rest. | |
| **Indexes.** `(tenant_id, identity_provider_subject)` unique. | |
| **Authorization.** Read: self, or an admin with the `user.read` permission, object-level checked. | |

### `roles`
`id` (PK), `tenant_id`, `name`, `description`. Platform ships a default role set per tenant at
provisioning; a tenant may define additional roles (Bank Admin Portal, §17.1).

### `permissions`
`id` (PK), `name`, `resource_type`, `action`. **Platform-global**, not tenant-scoped — the
enumeration of possible permissions is fixed by the platform; which permissions a given role has
is tenant-scoped, via `role_permissions`.

### `user_roles`
`user_id` (FK), `role_id` (FK), `tenant_id`, `granted_by`, `granted_at`, `expires_at` (nullable —
supports time-bound elevated access, architecture §6.5). **Authorization implication:** granting an
elevated role requires the two-person authorization rule (architecture §17.1) for any role carrying
a sensitive permission set, enforced at the Bank Admin Portal API layer, not merely a UI
convention.

### `policies`
`id` (PK), `tenant_id`, `name`, `policy_document` (JSONB — ABAC/PBAC rule set), `status`
(`draft`/`active`/`retired`), `version`. Feeds `configuration_versions` (§7) for its
version-lifecycle history.

### `sessions`
`id` (PK), `tenant_id`, `user_id`, `device_fingerprint`, `issued_at`, `expires_at`,
`revoked_at` (nullable), `revocation_reason`. **Sensitive fields.** None beyond device fingerprint
(hashed, not raw). **Note:** the session *token* itself is never stored in the relational database
in plaintext — only a hash of it, sufficient to validate a presented token without the database
being a source of usable tokens if compromised.

---

## 2. Customer Domain

### `customers`
`id` (PK), `tenant_id`, `bank_customer_reference` (the bank's own customer ID — an opaque
reference, not the platform's authority; Principle 2). **The platform does not store a
customer's full profile** — name, address, etc. remain in the bank's own CBS/CRM; this table
exists only to link a `users` row (the authenticated platform session) to the bank's own
reference, for object-level scope resolution (Part 4 §2, step 6).

### `customer_accounts`
`id` (PK), `tenant_id`, `customer_id` (FK), `bank_account_reference` (opaque, tokenized where it
appears in any log — PII/Tokenization Service, Part 1 §2.28). **This table is a cache of the
bank's own linkage, refreshed on a short TTL (Part 4 §2) — never the authoritative source.**
**Authorization implication:** this is the literal backing store for `SecurityContext.
customer_scope` (Part 4 §1).

---

## 3. Conversation Domain (architecture §11.1)

### `conversations`
`id` (PK), `tenant_id`, `user_id`, `session_id`, `module_id`, `access_scope` (JSONB),
`retention_policy_id` (FK → `retention_policies`), `legal_hold_id` (FK, nullable). **Indexes.**
`(tenant_id, user_id)`, `(tenant_id, session_id)`. **Authorization implication.** Every read
requires an object-level check that `requesting_user_id == conversations.user_id` OR the requester
holds an explicit privileged-access grant (architecture §11.4), itself logged.

### `messages`
`id` (PK), `conversation_id` (FK), `tenant_id` (denormalized for RLS efficiency), `role`
(`user`/`assistant`/`system`), `content` (PII-tokenized before storage — Part 1 §2.28), `citations`
(JSONB), `confidence_score`, `guard_results` (JSONB), `created_at`. **Sensitive fields.** `content`
— tokenized, never raw PII at rest. **Retention.** Per `conversations.retention_policy_id`;
deletion cascades to any derived summary/embedding record (architecture §11.8).

---

## 4. Document & Evidence Domain

### `documents`
`id` (PK), `tenant_id`, `source_uri`, `classification` (enum: `public`/`internal`/`confidential`/
`restricted`/`customer_sensitive`/`regulatory`/`executive`/`hr`/`compliance_security`,
architecture §12.2), `ingestion_path` (`A_unstructured`/`B_structured`), `content_hash` (for
duplicate detection), `retention_policy_id`, `legal_hold_id` (nullable). **Sensitive fields.**
`source_uri` may reveal internal paths — access-controlled the same as the document itself.

### `document_versions`
`id` (PK), `document_id` (FK), `version_number`, `effective_date`, `superseded_by`
(FK → `document_versions`, nullable). **Authorization implication.** A query for "the policy as of
date X" resolves the version whose `effective_date` range covers X — current and superseded
versions are never blended (architecture §12.2).

### `document_chunks` / `document_tables` (Path A intermediate representation)
`id` (PK), `document_version_id` (FK), `page`, `text`, `tables` (JSONB — position-preserving, per
the research core's table-rendering fix). Transient/intermediate — retained for re-processing
audit purposes, not directly queried at answer time.

### `propositions` (the frozen §5.1 contract, amended per architecture §4.2)
| Field | Notes |
|---|---|
| `proposition_id` (PK) | Stable, shared across all three indexes (dense/sparse/metadata). |
| `tenant_id` | **The §4.2 amendment** — added deliberately, versioned, re-verified against CoVe's numeric comparator before this schema ships. |
| `text`, `provenance_tier` (`retrieved`/`derived`/`external`), `ingestion_path` | Unchanged from the research core. |
| `numeric` (JSONB: `value`, `scale`, `currency`, `unit`, `period`) | Unchanged. |
| `confidence` (JSONB: `ocr_confidence`, `extraction_tier`) | Unchanged. |
| `metadata` (JSONB: `company`/entity, `filing_type`, `filing_date`, `section`, `page`, `source_uri`) | Unchanged. |
| `document_version_id` (FK) | New — links a proposition to the specific document version it was extracted from, supporting §document_versions above. |
| `classification` | Inherited from `documents.classification` at extraction time. |
| **Indexes.** `(tenant_id, proposition_id)` composite — the three-index invariant's key (architecture §4.3). | |
| **Authorization implication.** Retrieval (Part 1 §2.13) filters on `(tenant_id, classification ⊆ security_context.data_classification_clearance)` before any candidate is scored. | |

### `embeddings` (index reference table, not the vectors themselves)
`proposition_id` (FK), `tenant_id`, `embedder_model`, `embedder_revision`, `vector_store_namespace`
— a pointer record so the relational database can answer "which vector store namespace holds this
proposition's vector" without holding the vector itself (which lives in the Vector Database,
Part 1 §2.37).

---

## 5. Connector & Tool Domain

### `connectors`
`id` (PK), `tenant_id`, `connector_type` (`cbs`/`hr`/`crm`/`fraud`/`credit_bureau`/
`transaction_monitoring`/`auth_flow_step`), `transport`, `auth_strategy`, `encryption_strategy`,
`binding_config` (JSONB), `capability_manifest` (JSONB — which operations/writes this connector
supports, architecture §10.6), `status` (`draft`/`tested`/`active`/`disabled`), `version`.

### `connector_credentials`
`id` (PK), `connector_id` (FK), `credential_type` (`username`/`password`/`api_key`/`client_secret`/
`aes_key`/`aes_iv`/`certificate`/`private_key`), `secret_reference` (a pointer into Secrets
Management — **the actual value is never in this table**). **This table stores references
exclusively; there is no column in this schema capable of holding a raw credential value.**

### `tools`
`id` (PK), `name` (e.g. `get_account_balance`), `connector_id` (FK), `read_or_write`
(`read`/`write`), `requires_human_confirmation` (bool), `requires_step_up_auth` (bool).

### `tool_permissions`
`tool_id` (FK), `role_id` (FK), `tenant_id`. **Authorization implication.** A tool call is
rejected at the Tool/MCP Gateway if no `tool_permissions` row grants the caller's role access —
independent of the Connector Capability Manifest's own restriction (defense in depth, two
independent gates per architecture §10.6).

---

## 6. Agentic Domain

### `agent_tasks`
`id` (PK), `tenant_id`, `user_id`, `module_id`, `status` (`running`/`completed`/`incomplete`/
`escalated`), `execution_budget` (JSONB: `max_tool_calls`, `max_runtime_seconds`, `max_cost`,
`max_recursion`, `max_retries` — Part 9 defines the object fully), `started_at`, `ended_at`.

### `agent_steps`
`id` (PK), `agent_task_id` (FK), `step_number`, `step_type` (`plan`/`tool_call`/`tool_result`/
`revise`), `tool_id` (FK, nullable), `arguments` (JSONB, PII-tokenized), `result` (JSONB,
PII-tokenized), `authorized` (bool — the outcome of the Tool/MCP Gateway's independent check),
`created_at`. **This table is the full trajectory record**, replayable for audit (architecture
§12.7 of the original review).

---

## 7. Transaction Domain (Part 11 defines the full state machine)

### `transaction_intents`
`id` (PK), `tenant_id`, `user_id`, `module_id`, `idempotency_key` (unique per tenant),
`state` (`REQUESTED`/`AUTHORIZATION_PENDING`/`HUMAN_APPROVAL_PENDING`/`SUBMITTED`/`SUCCESS`/
`FAILED`/`UNKNOWN`/`RECONCILING`), `bank_reference` (nullable — populated once the bank's system
acknowledges), `created_at`, `resolved_at`.

### `transaction_operations`
`id` (PK), `transaction_intent_id` (FK), `connector_id` (FK), `attempt_number`, `request_payload`
(PII-tokenized), `response_payload` (PII-tokenized), `outcome`.

---

## 8. Human Oversight Domain

### `human_escalations`
`id` (PK), `tenant_id`, `trigger_reason` (`confidence`/`risk`/`policy`/`explicit_request`),
`module_id`, `related_conversation_id` (FK, nullable), `related_transaction_intent_id` (FK,
nullable), `queue`, `assigned_reviewer_id` (FK → `users`, nullable), `sla_deadline`, `status`
(`pending`/`approved`/`rejected`/`timed_out`/`reassigned`).

### `approvals`
`id` (PK), `escalation_id` (FK), `reviewer_id` (FK), `decision` (`approve`/`reject`), `reasoning`,
`decided_at`. **Authorization implication.** Insert is rejected by the Authorization Engine if
`reviewer_id == escalation.requester_user_id` or `reviewer_id` is the subject of the escalated
action (architecture §9.4) — enforced as a database-level check constraint in addition to the
application-level check, for defense in depth.

---

## 9. Audit Domain (Part 14 defines the hash-chain structure fully)

### `audit_events`
`id` (PK), `tenant_id`, `event_type`, `actor_user_id` (nullable — service-originated events have
no human actor), `resource_type`, `resource_id`, `decision` (nullable), `metadata` (JSONB,
PII-tokenized), `created_at`, `previous_hash`, `current_hash`. **No `DELETE` grant exists on this
table for any application role** (architecture §14.3). **Indexes.** `(tenant_id, created_at)`,
`(tenant_id, actor_user_id)`.

### `audit_chain` (optional, backend-dependent — see Part 14 for the architecture-vs-implementation
distinction)
Periodic checkpoint records anchoring a batch of `audit_events` hashes externally.

---

## 10. Data Governance Domain

### `classifications`
Platform-global lookup table enumerating the nine classification levels (architecture §12.2) — not
tenant-editable, since the taxonomy itself is a platform-wide invariant.

### `retention_policies`
`id` (PK), `tenant_id`, `data_class` (`document`/`conversation`/`audit`/`pii_vault`/`cache`),
`retention_days`, `default_conservative` (bool). **Authorization implication.** Only a Bank Admin
with the sensitive-configuration permission (two-person rule, architecture §17.1) may shorten a
retention period; lengthening does not require the second approval.

### `legal_holds`
`id` (PK), `tenant_id`, `target_type` (`conversation`/`document`/`customer_record`), `target_id`,
`reason`, `set_by`, `set_at`, `lifted_at` (nullable). **Authorization implication.** Any deletion
or retention-expiry job checks for an active `legal_holds` row against its target before
proceeding (architecture §12.6) — this check is a hard gate, not advisory.

---

## 11. Model / Prompt / Configuration Governance Domain (Part 16 defines the lifecycle fully)

### `models`
`id` (PK), `provider`, `model_name`, `pinned_version`, `retirement_status`.

### `model_versions`
`id` (PK), `model_id` (FK), `version_string`, `approved_tenants` (JSONB array of `tenant_id`),
`evaluation_results` (JSONB — gate-suite pass/fail), `created_at`.

### `prompts`
`id` (PK), `call_site`, `base_prompt`.

### `prompt_versions`
`id` (PK), `prompt_id` (FK), `version_number`, `content`, `tenant_extensions` (JSONB, nullable —
the approved extension point only, architecture §18.2).

### `configuration_versions`
`id` (PK), `tenant_id` (nullable for platform-global config), `domain` (`confidence_thresholds`/
`escalation_rules`/`pii_policy`/`connector_config`/`agent_limits`/etc.), `version_number`,
`content` (JSONB), `status` (`draft`/`validated`/`evaluated`/`approved`/`active`/`rolled_back`),
`activated_at`. **This is the single generic versioned-registry table backing every "Draft →
Validation → Evaluation → Approval → Production → Rollback" lifecycle described in Part 16** —
domains share the table rather than each inventing its own versioning mechanism.

---

## 12. Licensing Domain

### `licenses`
`id` (PK), `tenant_id`, `signed_entitlement_document` (the full signed license artifact,
architecture §22.2), `issued_at`, `expires_at`, `deployment_type`. **Platform-global signing key
reference lives in Secrets Management, never in this table.**

### `plans`
`id` (PK), `name`, `base_price`, `included_modules` (JSONB), `seat_policy`, `deployment_options`,
`support_tier_defaults`. **Platform-global**, editable via the Plan Registry (Part 1 §2.42) with
no code release required.

### `feature_entitlements`
`tenant_id`, `feature_key`, `enabled` (bool), `capacity_band` (JSONB, nullable — for custom
Business/Enterprise deals, architecture §22.7). This is the resolved, per-tenant expansion of a
`plans` template, potentially customized beyond what the template specifies.

---

## Summary — Authorization Implications Cutting Across the Whole Schema

1. **Every table with a `tenant_id` column has an RLS policy** — this is not optional per-table;
   it is a migration-time requirement enforced by a schema-linter check in CI (Part 23 — a test
   that fails the build if a new tenant-scoped table lacks an RLS policy).
2. **No table anywhere in this schema is capable of storing a raw secret, token, or credential
   value** — every such field is a `secret_reference` string pointing into Secrets Management.
3. **No table governed by a retention policy exposes a hard-delete path from any API** — deletion
   is always a state transition checked against `legal_holds` first.
4. **`audit_events` has no delete grant for any role, including platform superuser equivalents.**

---

*End of Part 5. Part 6 (Vector / RAG Data Model) follows immediately.*
