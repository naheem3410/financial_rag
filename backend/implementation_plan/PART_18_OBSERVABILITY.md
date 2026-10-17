# Master Technical Architecture and Implementation Plan
## Part 18 — Observability

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §15. These four instruments are kept explicitly
distinct because conflating them is exactly where governance requirements get accidentally
under-served (an audit requirement satisfied by "we have logs" is not actually satisfied).

---

## Distinguishing the Four

| Instrument | Answers | Owned by |
|---|---|---|
| **Logs** | "What did this service do, at this moment, in operational terms?" | Each service, shipped to a central log store |
| **Metrics** | "How is the platform performing in aggregate, over time?" | Infrastructure monitoring stack |
| **Traces** | "What was the exact sequence of LLM calls, tool calls, and retrieval steps for this one request?" | Langfuse (LLM/agent-specific) + a general distributed-tracing backend (infra-level) |
| **Audit** | "What security- or compliance-relevant event occurred, who caused it, and can it be proven unaltered?" | Audit Service (Part 14) — a fundamentally different guarantee (tamper-evidence) than the other three, which optimize for operational visibility, not evidentiary integrity |

**A trace is not an audit record, even though both may reference the same underlying request.**
A trace can be sampled, can be lossy under load, and carries no tamper-evidence guarantee — it
exists for debugging and performance analysis. An audit record is complete-by-requirement,
append-only, and hash-chained (Part 14). No governance or compliance claim in this plan is
satisfied by trace data alone; where a claim requires proof, it is backed by the Audit Service.

---

## Logs

- **Data captured.** Request/response metadata (not body content by default), service-level
  operational events, error conditions.
- **PII handling.** Redacted before the log line is written (Part 13 §5) — the PII/Tokenization
  Service runs upstream of logging, not as a post-hoc scrubber applied to already-written logs.
- **Tenant isolation.** Every log line is tagged `tenant_id`; access to query logs is scoped
  through the same identity/authorization system as everything else (Part 1 §2.40) — not a
  separately unguarded tool a support engineer could query without going through Support/
  Break-glass Tooling (Part 15 §B).
- **Retention.** Configurable, typically shorter than audit retention (logs are an operational
  aid, not the evidentiary record) — a Business Decision Required for the exact default.
- **Access control.** Platform Admin Console, itself subject to the internal least-privilege rule
  (Part 1 §2.42) — a support engineer's log access is scoped to the ticket/session they are
  investigating, per the redacted-by-default rule (Part 15 §B).

## Metrics

- **Data captured.** Availability, latency, token consumption, cost, error rates, tool-failure
  rates, authentication/authorization failure rates, prompt-injection detection counts, anomalous
  behavior signals, cross-tenant access *attempt* counts (Part 3, Boundary 4's logging
  requirement feeds this), connector failure rates, agent-loop/budget-breach counts, confidence
  degradation trends.
- **PII handling.** Metrics are aggregates by construction — no individual PII value is ever a
  metric's own content, only counts/rates/durations.
- **Tenant isolation.** Aggregated **across** tenants for the platform-wide cross-tenant
  observability view (Part 1 §2.40) is explicitly permitted and useful (it's how the platform
  detects a cross-tenant access anomaly at all), but the platform-wide view never exposes any one
  tenant's *content* — only counts and rates, which by construction do not reveal what a specific
  request contained.
- **Retention.** Long enough to support trend analysis (typically longer than raw logs, shorter
  than audit) — implementation choice.
- **Access control.** Platform Admin Console's cross-tenant observability dashboard (Part 1
  §2.42); a per-tenant metrics view is also available through the Bank Admin Portal, scoped to
  that tenant only.
- **Governance ownership.** This instrument is the explicit, named implementation of the AI
  Governance pillar's **AI Performance Monitoring** capability (`ENTERPRISE_PLATFORM_
  ARCHITECTURE.md` §1.1) — no separate component exists for it, and none is needed. The
  per-tenant metrics view surfaced through the Bank Admin Portal, plus the platform-wide
  cross-tenant anomaly signals in Part 1 §2.40, together constitute the platform's answer to that
  named governance capability. This binding is carried into Part 24's traceability matrix as its
  own row.

## Traces

- **Data captured.** Full LLM call sequence (prompt version, model version, token counts,
  latency), tool-call sequence (Agent Orchestrator trajectories, Part 9 §4), retrieval steps.
  Langfuse is the aggregation layer over the OpenAI Agents SDK's native trace spans (architecture
  §2.9).
- **PII handling.** **Redaction happens before export to Langfuse, not after** (architecture
  §15.1) — the identical PII/Tokenization Service pipeline that guards the LLM's own input guards
  what gets traced; there is no separate "redact traces afterward" step, because that would leave
  a window where raw content briefly exists in the trace store.
- **Tenant isolation.** Access to view traces is scoped through the same identity/authorization
  system as everything else — never a separately unguarded observability tool reachable outside
  the platform's own authentication (architecture §15.1's explicit requirement).
- **Retention.** Configurable per tenant, aligned with the tenant's own data-retention policy for
  conversation-adjacent data, since a trace can reveal conversation content shape even when
  redacted (query patterns, timing) — treated as sensitive by default.
- **Access control.** Platform Admin Console (Model & Prompt Governance section, Part 1 §2.42)
  for debugging/evaluation purposes; a bank's own admin may see their own tenant's traces through
  the Bank Admin Portal if that capability is enabled for their tier.

## Audit

Fully specified in Part 14 — included here only to complete the four-instrument distinction. The
defining difference: **tamper-evidence** (hash-chained, append-only, no delete grant for any
role) — a guarantee none of Logs, Metrics, or Traces provide or need to provide, since they serve
operational visibility, not evidentiary proof.

---

## Cross-Cutting Rule

**Anything that could leak tenant content is treated as sensitive by default** (architecture
§25.1's original review question, "can observability data itself leak customer data" —
answered structurally here): the redaction-before-export discipline applies uniformly to Logs and
Traces; Metrics are aggregate by construction and therefore structurally incapable of leaking a
specific value; Audit records are PII-tokenized in their `metadata` field the same as every other
store (Part 5 §9).

---

*End of Part 18. Part 19 (Disaster Recovery) follows immediately.*
