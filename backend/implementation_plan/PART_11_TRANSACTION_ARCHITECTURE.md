# Master Technical Architecture and Implementation Plan
## Part 11 — Transaction Architecture

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §8.11 (transaction intent/execution separation),
§10.1 item 6 (idempotency), §10.10 (timeout with unknown outcome). Builds on Part 5 §7
(`transaction_intents`, `transaction_operations`).

---

## 1. The State Machine

```
                    REQUESTED
                        │
                        ▼
             AUTHORIZATION_PENDING ──────────────┐
                        │                        │  (object-level / policy check fails)
                        ▼                        ▼
           HUMAN_APPROVAL_PENDING             FAILED
           (only if the action requires
            human confirmation per
            architecture §9 / §16.8)
                        │
              ┌─────────┼─────────┐
              ▼                   ▼
          approved              rejected ──────────────▶ FAILED
              │
              ▼
           SUBMITTED  ────────────────────────────────────┐
              │                                            │
      ┌───────┼────────────┐                               │  (bank system times out /
      ▼       ▼            ▼                               │   connection lost before a
   SUCCESS  FAILED      UNKNOWN                             │   definitive response arrives)
      │        │            │                               │
      │        │            ▼                               │
      │        │      RECONCILING ◀───────────────────────────
      │        │            │
      │        │      (status query using the same
      │        │       idempotency_key, per §4 below)
      │        │            │
      │        │    ┌───────┼───────┐
      │        │    ▼       ▼       ▼
      │        │  SUCCESS FAILED  still UNKNOWN
      │        │                  (escalate to human,
      │        │                   §5 below)
      ▼        ▼
  (terminal states — no further transition)
```

## 2. Intent — What the AI Produces, and What It Does Not

The AI never produces a command to execute; it produces a `transaction_intents` row (Part 5 §7) in
state `REQUESTED`. This is the concrete implementation of Principle 2 and architecture §8.11:

```python
class TransactionIntent(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    module_id: str
    idempotency_key: str          # generated exactly once, at REQUESTED, never regenerated
    operation: str                # e.g. "fund_transfer"
    parameters: dict[str, Any]    # PII-tokenized where applicable
    state: TransactionState
    bank_reference: str | None    # populated once the bank's system acknowledges (state=SUBMITTED
                                   # onward)
```

**The intent is a request for the bank's own authorization pipeline to consider — it carries no
authority of its own.** State transitions from `AUTHORIZATION_PENDING` onward are driven by the
Authorization Engine's object-level check (does this user/account combination pass), the bank's own
step-up authentication and fraud/risk checks (external to the platform, Principle 2), and, where
required, the Human Escalation Service.

## 3. Idempotency Key — Generation and Reuse Rule

```
1. On transition to REQUESTED, generate idempotency_key = uuid4() exactly once.
2. Every subsequent Connector Framework call for this intent — the initial SUBMITTED attempt,
   any retry after a transient failure, and any RECONCILING status query — uses this SAME key,
   read from `transaction_intents.idempotency_key`, never a freshly generated one.
3. The key is passed to the bank's system via the connector's HeaderConfig.generated field
   (Part 10 §1.6) as "Idempotency-Key" or the transport-appropriate equivalent binding.
```

**This is the exact mechanism preventing duplicate execution (§16.6 of the architecture review):**
a bank's own system, receiving two requests with the identical idempotency key, is expected (per
standard idempotent-API design, which the connector's `capability_manifest` should record as a
prerequisite for any connector supporting write operations) to execute the underlying operation at
most once and return the same result for both requests. The platform's own discipline is never
regenerating the key — it does not rely on the bank's system alone, but it also cannot manufacture
idempotency the bank's system doesn't itself support; a connector for a write operation whose
target system does not support idempotency keys must be flagged at connector-registration time as
a higher-risk connector requiring additional reconciliation tooling (see §4).

## 4. Timeout and Reconciliation — the UNKNOWN → RECONCILING Path

Architecture §10.10, made concrete:

```
1. A SUBMITTED transaction_intent's connector call times out (no response received within
   RequestLifecycleConfig.timeout_seconds, Part 10 §1.9) before a definitive SUCCESS/FAILED
   response arrives.
2. State transitions to UNKNOWN. The platform does NOT assume success, and does NOT blindly
   retry the original write call — retrying a write whose outcome is unknown, using a NEW
   attempt, is exactly the double-execution risk this whole design exists to prevent.
3. State transitions to RECONCILING. The Connector Framework issues a STATUS QUERY (a distinct,
   read-only operation — e.g. "get_transaction_status(idempotency_key)" — declared separately in
   the connector's capability_manifest from the original write operation) using the SAME
   idempotency_key.
4. The bank's response to the status query resolves the transaction:
   - If the bank reports the operation succeeded (using that idempotency key), state → SUCCESS.
   - If the bank reports it never received/executed the operation, state → FAILED (safe to
     retry as a genuinely new attempt, since the bank confirms nothing happened).
   - If the bank's status query itself times out or the bank cannot answer, state remains
     RECONCILING and is escalated per §5 — never silently resolved to any other state.
```

## 5. Human Escalation on Unresolved Reconciliation

A `RECONCILING` transaction that cannot be resolved within a configurable window (or whose status
query itself fails) is escalated via the Human Escalation Service (Part 1 §2.26) with the message
surfaced to the human reviewer being **"status unknown, verify with your bank"** — architecture
§10.10's stated exact behavior — never a system-generated guess in either direction. The escalation
carries the full `transaction_operations` history (Part 5 §7) so the reviewer can see every attempt
made.

## 6. Audit

Every state transition on a `transaction_intents` row is written as an `audit_events` entry
(Part 1 §2.27, Part 5 §9) — `REQUESTED`, every `AUTHORIZATION_PENDING` check outcome, every human
approval/rejection, every `SUBMITTED` attempt (with its `transaction_operations` row), and the
terminal state. This gives a complete, replayable record answering "what happened to this
transaction and why" without needing to reconstruct it from application logs.

---

*End of Part 11. Part 12 (Document Ingestion) follows immediately.*
