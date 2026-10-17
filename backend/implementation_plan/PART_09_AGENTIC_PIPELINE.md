# Master Technical Architecture and Implementation Plan
## Part 9 — Agentic Pipeline

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §2.1 (Agentic Research Mode), §2.3 (module
gating), and the agentic-hard-limits decision resolved in §23.3 of that document. Applies only to
modules statically assigned Agentic mode (Regulatory Intelligence, Fraud Investigation Support,
and the Management pillar's exploratory modules) — this pipeline is structurally unreachable from
any Controlled module.

---

## Pipeline Diagram

```
Request
     │
     ▼
[1] Authorization (module-mode gate) ──── API Gateway checks the REQUESTING MODULE's static
     │                                    mode assignment (architecture §2.3). If the module is
     │                                    not Agentic-allowed, this pipeline is never entered —
     │                                    there is no runtime code path that promotes a
     │                                    Controlled-mode request into this pipeline, regardless
     │                                    of query content, user role, or LLM output.
     ▼
[2] Agent task creation ────────────────── Agent Orchestrator (Part 1 §2.22) creates an
     │                                    `agent_tasks` row (Part 5 §6), attaches the immutable
     │                                    SecurityContext (Part 4) — the same context used
     │                                    throughout the task, never re-derived mid-execution
     ▼
[3] Execution budget attached ──────────── See §2 below — the ExecutionBudget object is
     │                                    constructed here, once, from tenant configuration,
     │                                    and stored on the `agent_tasks` row
     ▼
[4] Planning (LLM) ──────────────────────── LLM Gateway, a registered call site — the agent
     │                                    proposes a next step (answer directly, or call a tool)
     ▼
[5] Tool selection (LLM proposes) ──────── The LLM's output is a PROPOSAL: {tool_name,
     │                                    arguments}. This proposal has no authority of its own.
     ▼
[6] Authorization (tool call) ──────────── Tool/MCP Gateway (Part 1 §2.23) independently checks:
     │                                    (a) is this tool in `tool_permissions` for the
     │                                    SecurityContext's role, (b) does the Connector
     │                                    Capability Manifest declare this operation supported,
     │                                    (c) does the argument (e.g. an account reference)
     │                                    fall within `SecurityContext.customer_scope` /
     │                                    `abac_attributes` — per Part 4 §4's enforcement table.
     │                                    THIS STEP, NOT THE LLM'S PROPOSAL, IS THE
     │                                    AUTHORIZATION DECISION.
     │
     ├── DENIED ──▶ logged as an `agent_steps` row with `authorized=false`, consumes one unit
     │              of `max_retries`, control returns to [4] with the denial visible to the
     │              agent's next planning step (so it can try a different, authorized approach
     │              or conclude it cannot proceed) — a denial is never silently retried with the
     │              same call.
     │
     ▼ (authorized)
[7] Tool call ───────────────────────────── Connector Framework (Part 1 §2.24) executes the call.
     ▼
[8] Response validation ─────────────────── Tool/MCP Gateway schema-validates the response
     │                                    against the tool's declared contract (architecture
     │                                    §8.4, §10.11) BEFORE it is shown to the agent's next
     │                                    planning step. A response that fails validation is
     │                                    discarded and replaced with an explicit "tool response
     │                                    invalid" signal — never passed through in raw form,
     │                                    since an unvalidated response is untrusted data that
     │                                    could itself carry a prompt-injection payload.
     ▼
[9] State update ────────────────────────── Agent Orchestrator writes an `agent_steps` row
     │                                    (Part 5 §6) recording the full step — this is the
     │                                    trajectory record, replayable for audit (architecture
     │                                    §12.7 of the original review).
     │
     │  BEFORE returning to planning, the orchestrator checks EVERY budget dimension (§2 below)
     │  against the task's cumulative counters. Any breach short-circuits to [11] regardless of
     │  what the LLM's next planned step would have been.
     ▼
[10] Next step ──────────────────────────── Loop back to [4], or proceed to completion if the
     │                                    agent's own plan concludes it has enough evidence.
     ▼
[11] Completion / Escalation ────────────── Either:
                                            (a) Task completes normally — result assembled,
                                                confidence scored (same Confidence Engine as the
                                                Fixed pipeline, Part 1 §2.20), citation bound,
                                                audited — same downstream steps as Part 8's
                                                [11]–[16].
                                            (b) A budget limit was reached — task marked
                                                `incomplete`, an explicit "incomplete — limit
                                                reached" result is returned, never presented as
                                                if it were a complete answer.
                                            (c) A high-risk action was proposed (e.g. a write) —
                                                routed to Human Escalation Service (Part 1 §2.26),
                                                same fail-closed rules as architecture §9.
```

---

## 1. The Execution Budget Object

```python
class ExecutionBudget(BaseModel):
    max_tool_calls: int = 10
    max_runtime_seconds: int = 300          # 5 minutes
    max_cost_usd: Decimal = Decimal("2.00") # configurable per tenant currency/ceiling
    max_recursion_depth: int = 3
    max_retries_per_tool: int = 2

    # Tenant/module override — these are DEFAULTS (architecture §23.3), never a hard platform
    # ceiling in the sense of §13.2's confidence floor. A tenant may tighten these; loosening
    # beyond a platform-defined maximum (a separate, non-negotiable ceiling set to prevent
    # runaway cost regardless of tenant configuration) is rejected at configuration time by the
    # Configuration Service (Part 1 §2.30).
```

**Constructed once, at task creation (step 3), from `configuration_versions` (domain =
`agent_limits`, tenant- and module-scoped). Never reconstructed or widened mid-task.**

## 2. Exactly Where Each Limit Is Enforced

| Limit | Enforcement point | Mechanism |
|---|---|---|
| `max_tool_calls` | Agent Orchestrator, step [9] | Increments a counter on `agent_tasks` on every `agent_steps` row of type `tool_call`; compares against `execution_budget.max_tool_calls` before allowing step [10] to loop back into another tool-call proposal. |
| `max_runtime_seconds` | Agent Orchestrator, step [9], and a background watchdog | Compares `now() - agent_tasks.started_at`; additionally, a scheduled watchdog process independently terminates any `agent_tasks` row still `running` past its deadline, so a stuck loop cannot survive by never reaching its own check-in point. |
| `max_cost_usd` | Agent Orchestrator, step [9] | Accumulates the per-call cost reported by the LLM Gateway's Agents SDK tracing data (Part 1 §2.2) after every LLM call within the task; compared before permitting another planning call. |
| `max_recursion_depth` | Agent Orchestrator, step [4]/[9] | A task-scoped counter incremented whenever a planning step's proposed action would itself spawn a sub-task or a nested tool-call chain; compared before allowing the nesting to proceed. |
| `max_retries_per_tool` | Tool/MCP Gateway, step [6] denial path | Per-tool counter within the task; a tool that has been denied or has failed validation `max_retries_per_tool` times is marked unavailable for the remainder of the task, forcing the agent's next planning step to choose differently or conclude. |

**Prompt instructions are not the enforcement mechanism for any of the above — every one of these
checks is a comparison performed in Agent Orchestrator or Tool/MCP Gateway code, against counters
stored on the `agent_tasks`/`agent_steps` rows, independent of what the LLM was told in its system
prompt.** This directly satisfies the architecture review's explicit requirement (§53 of the
review, "do not accept 'the prompt tells it not to'").

## 3. Stuck-Agent Recovery

If a task is terminated by the watchdog (runtime limit) or by budget exhaustion mid-plan:

1. The task's `agent_tasks.status` is set to `incomplete`.
2. Any tool call already dispatched and awaiting response is allowed to complete (to avoid an
   inconsistent partial write on the external system's side) but no new tool call is issued.
3. The partial trajectory is preserved in `agent_steps` for audit and for a human reviewer to
   inspect if the incomplete result is escalated.
4. The caller receives an explicit incomplete result, never a result that looks complete.

## 4. Trajectory Replay for Audit

Every `agent_steps` row is immutable once written (append-only, same principle as the audit log,
Part 14). A full task's trajectory can be reconstructed in order by
`SELECT * FROM agent_steps WHERE agent_task_id = :id ORDER BY step_number` — this is the
mechanism behind architecture §12.7 ("can an agent's trajectory be replayed for audit") and feeds
directly into Part 23's agentic-safety test matrix (tool misuse, tool loops, cost runaway,
unauthorized action, trajectory correctness).

## 5. Why Controlled Modules Cannot Escalate Into This Pipeline

Restated because it is the single most important guarantee in this part: the module-mode
assignment (architecture §2.3) is read by the API Gateway at step [1] from static configuration
tied to the module's identity, resolved before the request reaches any component capable of
invoking an LLM. There is no code path in this pipeline, nor in Part 8's Fixed pipeline, through
which a Controlled-mode request is handed to the Agent Orchestrator — the two pipelines are
separate entry points from the API Gateway, not a single pipeline with a runtime branch that an
LLM's output could influence.

---

*End of Part 9. Part 10 (MCP / Connector Architecture) follows immediately.*
