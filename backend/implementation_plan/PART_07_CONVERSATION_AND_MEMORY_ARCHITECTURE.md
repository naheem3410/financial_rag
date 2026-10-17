# Master Technical Architecture and Implementation Plan
## Part 7 — Conversation & Memory Architecture

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §11 in full technical detail. Builds on Part 1
§2.9 (Conversation Service) and §2.10 (Context Builder), and Part 5 §3 (schema).

---

## 1. The Object Chain

```
User
  │  (1 user, many sessions over time)
  ▼
Session
  │  (1 session, typically one continuous login period; may span multiple conversations)
  ▼
Conversation
  │  (1 conversation, many messages, scoped to exactly one module_id)
  ▼
Message
  │  (each message may carry citations, confidence, guard_results)
  ▼
Context (ephemeral — assembled per-request by the Context Builder, never stored as its own entity)
```

**Context is not a persisted object.** It is the output of `Context Builder.build_context(scope)`
(Part 1 §2.10) — recomputed on every request from `Conversation`/`Message` records plus
freshly-authorized retrieval evidence, never cached as a standing "memory" object unless the
tenant has explicitly enabled cross-session memory (§5 below).

## 2. Ownership

| Level | Owning field | Set by |
|---|---|---|
| Tenant | `conversations.tenant_id` | Resolved from `SecurityContext.tenant_id` at conversation creation — never client-supplied |
| User | `conversations.user_id` | Resolved from `SecurityContext.user_id` |
| Session | `conversations.session_id` | Resolved from `SecurityContext.session_id` |
| Conversation | `conversations.id`, `conversations.module_id` | Created once per distinct conversational thread; `module_id` fixes which product module (and therefore which orchestration mode, Controlled or Agentic) this conversation belongs to for its entire lifetime — a conversation cannot change module mid-thread |
| Access scope | `conversations.access_scope` (JSONB snapshot of the classification/ABAC scope active at creation) | Recorded at creation for audit; **re-evaluated live on every read**, never trusted from this snapshot alone (§4 below) |

## 3. Tenant Isolation

Identical mechanism to every other tenant-scoped table (Part 5, Part 3 Boundary 4): row-level
security keyed to `tenant_id`, plus the same defense-in-depth layering — a conversation query is
never satisfiable across a tenant boundary even given an application-layer bug, because the
database itself refuses the row.

## 4. The Exact Enforcement Preventing User A → User B's Conversation

This is the specific mechanism requested — stated as a sequence, not a principle:

```
1. User A issues: GET /conversations/{conversation_id}

2. API Gateway attaches User A's SecurityContext (tenant_id=T, user_id=A) to the request.

3. Conversation Service receives the request and, BEFORE returning any row, calls the
   Authorization Engine:

       authorize(
           security_context = {tenant_id: T, user_id: A, ...},
           resource = {type: "conversation", id: conversation_id},
           action = "read"
       )

4. The Authorization Engine's object-level check (Part 1 §2.6) loads the conversation's actual
   owner:

       SELECT tenant_id, user_id FROM conversations WHERE id = :conversation_id

   and evaluates:

       decision = ALLOW  if  (conversation.tenant_id == T AND conversation.user_id == A)
                  OR (a valid, unexpired privileged-access grant exists for
                      (grantee=A, target_conversation=conversation_id))
                  ELSE DENY

5. If the conversation belongs to User B (conversation.user_id == B, B != A) and no privileged
   grant exists, the decision is DENY.

6. Conversation Service returns 403/404 (indistinguishable response — never reveals whether the
   conversation exists but is forbidden, versus does not exist at all, since even that
   distinction is information leakage) and logs the attempt to the Audit Service.

7. The database's own row-level security policy on `conversations` provides a second, independent
   layer beneath this: even if step 3's application-level check were somehow skipped due to a
   bug, the underlying SQL connection's session variable (tenant_id=T) means a query for a
   conversation in a different tenant returns zero rows regardless. Within the same tenant, RLS
   alone does not stop User A from reading User B's conversation (both share tenant_id=T) — this
   is why the object-level check in step 3/4 is mandatory and not optional; RLS defends the
   tenant boundary, the Authorization Engine defends the user boundary within it.
```

**This exact sequence is what Part 23's adversarial test "User A cannot retrieve User B's
conversation" verifies — the test asserts the DENY at step 5 and the audit log entry at step 6,
not merely that the API "seems to work correctly" in the happy path.**

## 5. Retrieval, Memory, and Defaults

- **Same-session context** is retrieved by the Context Builder scoped to
  `(tenant_id, user_id, session_id, conversation_id)` — on by default, since it is how a single
  multi-turn conversation functions at all.
- **Cross-session memory, personalized memory, and conversation embeddings are OFF by default**
  (architecture §11.6), enabled only via explicit tenant configuration
  (`configuration_versions`, domain = `memory_policy`).
- **If enabled, memory follows the exact ingestion pipeline of §11.7**: PII detection/redaction →
  authorization classification → memory eligibility policy → embedding → **a user-scoped memory
  index** — never one shared vector index for all users of a tenant with authorization applied
  only at query time. This is a physically/logically separate namespace per user within the
  tenant's vector store partition (extending the tenant-namespace pattern of Part 1 §2.37 down one
  more level).

## 6. Retention and Deletion

- `conversations.retention_policy_id` (Part 5 §3) governs the retention window, tenant-configured,
  defaulting conservatively.
- **Deletion is cascade-aware by construction, not by convention.** Deleting a conversation
  triggers deletion of: its `messages` rows, any derived summary record, any memory-index entry
  keyed to that conversation (if cross-session memory was enabled), and any cache entry whose key
  included that conversation's ID (architecture §11.8). This cascade is implemented as a single
  transactional deletion job, not a set of independently-scheduled cleanup tasks that could drift
  out of sync with each other.
- **Legal holds** (Part 5 §10) block this deletion job at its first step — the job checks
  `legal_holds` for the conversation's ID before proceeding, and if a hold exists, the deletion
  request is queued/rejected rather than partially executed.

## 7. Cache Interaction

Per Part 6 §4 and architecture §11.5/§14.5: a conversation-derived cache entry's key includes
`tenant_id`, `user_id` (or the exact resolved access scope), `conversation_id` where relevant, and
`document/corpus version`. Highly sensitive transactional exchanges within a conversation are
excluded from caching entirely, following the same rule as any other sensitive query.

## 8. Audit Interaction

Every conversation read, especially any privileged-access read of another user's conversation
(§4, step 4's grant path), is logged to the Audit Service with the reason for the grant
(architecture §11.4). Conversation *content* itself is never written into an audit event in raw
form — audit records reference `conversation_id`/`message_id`, not the message text, keeping the
audit trail itself free of the same PII the conversation content was tokenized to protect.

## 9. Privileged Access Grants (the mechanism behind §4's step 4 exception)

A separate table, `conversation_access_grants` (extending Part 5 §3): `grantee_user_id`,
`target_conversation_id`, `granted_by`, `reason`, `expires_at`. Populated only through:

- The Human Escalation Service (Part 1 §2.26) granting the assigned reviewer read access to the
  specific escalated conversation, scoped to that escalation's duration.
- The Support/Break-glass Tooling (Part 1 §2.44) granting a Platform Operator time-bound access
  under an active break-glass consent record.

**No other code path inserts into this table.** In particular, an administrator role does not
receive standing, ungated access to conversations by virtue of being an administrator
(architecture §11.4's explicit rejection of that pattern).

---

*End of Part 7. Part 8 (RAG Request Pipeline) follows immediately.*
