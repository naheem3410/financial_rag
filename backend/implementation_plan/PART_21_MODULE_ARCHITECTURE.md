# Master Technical Architecture and Implementation Plan
## Part 21 — Module Architecture

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §23.6's module-by-module review template, applied
now only to the MVP set. Every other module in §1.1's five-pillar tree is deferred and specified
using this same template when it is actually scoped for build — not before, per the architecture
document's explicit instruction against speculative full specification (§23.5).

---

## MVP Module Set

Chosen to exercise every distinct architectural shape the platform needs to prove before wider
rollout: an internal-only Controlled module (lowest risk), a customer-facing Controlled module
(exercises the full auth/PII/connector stack), a high-stakes Banking Operations Controlled module
(exercises the strictest confidence/escalation posture), and one Agentic-mode module (exercises
the second orchestration lane). Document Intelligence is included because it already exists as
the research core and is the reference implementation the other four are measured against.

1. **Document Intelligence** (Banking Operations) — already built (research codebase)
2. **Enterprise Knowledge Assistant** (Employee Experience)
3. **AI Banking Assistant** (Customer Experience)
4. **Compliance Assistant** (Banking Operations)
5. **Regulatory Intelligence** (Management/Banking Operations boundary — first Agentic module)

---

## 1. Document Intelligence

1. **Mode.** Controlled.
2. **Data accessed.** Ingested filing/policy documents (Path A/B), classified per architecture
   §12.2, scoped to the requesting user's classification clearance.
3. **Tools accessed.** `search_knowledge_base` (internal retrieval MCP tool, Part 1 §2.4) only —
   no external connector required for this module's core function.
4. **Writes performed.** None — this module is read-only by nature.
5. **Roles permitted.** Employee, Compliance Officer, Auditor (module-specific role set, tenant-
   configurable).
6. **Object-level restrictions.** Classification-based filtering only (no per-customer scoping
   needed, since this module operates over institutional documents, not customer records).
7. **Confidence threshold.** Platform floor + tenant policy for the "internal knowledge" category
   (architecture §13.2's illustrative default range applies here, tenant-set in practice).
8. **Escalation triggers.** CoVe failure on a material numeric claim; confidence below threshold.
9. **Evidence requirements.** Every claim traces to a cited `proposition_id`; numeric claims pass
   the verbatim guard (inherited research-core behavior).
10. **Data leaving the tenant.** None beyond the tenant's own chosen LLM provider call (architecture
    §22.1's BYO-LLM rule).
11. **Model requirements.** As configured per call site (Model Registry, Part 16 §3) — inherits the
    research core's pinned `gpt-4.1-mini`-class model for segmentation/generation call sites.
12. **Failure modes.** Retrieval unavailable → abstention; guard failure → drop the fact, never
    repair (inherited rule).
13. **Evaluation metrics.** The existing FinanceBench-Collision three-arm comparison (architecture
    §16.1) — this module's evaluation harness already exists and is extended, not rebuilt.
14. **Regulatory considerations.** Document confidentiality classification must match the source
    filing's actual sensitivity (a public 10-K is `public`; an internal credit memo referencing
    the same customer is `confidential`/`restricted`).
15. **Additional guardrails.** None beyond the inherited numeric/period-verbatim guards and CoVe.

## 2. Enterprise Knowledge Assistant

1. **Mode.** Controlled.
2. **Data accessed.** Internal policy documents, HR/IT knowledge base articles, classified
   `internal` by default (never `customer_sensitive` or `restricted` — this module's index scope
   is deliberately narrower than Document Intelligence's).
3. **Tools accessed.** `search_knowledge_base`, scoped to the `internal` classification tier only
   at the retrieval pre-filter (Part 6 §3) — this module's queries never reach `restricted` or
   `customer_sensitive` propositions even if the requesting employee happens to hold that
   clearance elsewhere, since the module's own scope is a hard ceiling, not merely the user's own
   clearance.
4. **Writes performed.** None.
5. **Roles permitted.** Any authenticated employee.
6. **Object-level restrictions.** None beyond the classification ceiling in item 3 — this is
   intentionally the lowest-friction module, appropriate for an early, low-risk MVP entry.
7. **Confidence threshold.** Lower stakes than Document Intelligence — a lower default floor is
   appropriate, tenant-set.
8. **Escalation triggers.** Confidence below threshold only (no transaction/PII-changing action
   possible in this module, so the write/policy/PII-anomaly triggers of architecture §9.1 are
   structurally inapplicable here).
9. **Evidence requirements.** Citation to the source policy/knowledge-base article.
10. **Data leaving the tenant.** Same as Document Intelligence.
11. **Model requirements.** Same call-site pattern as Document Intelligence, potentially a cheaper
    model given lower stakes — a tenant-configurable choice, not a platform mandate.
12. **Failure modes.** Same as Document Intelligence.
13. **Evaluation metrics.** A synthesized golden dataset (architecture §16.7) — no existing
    benchmark like FinanceBench applies here, so realistic internal-knowledge Q&A fixtures are
    built specifically for this module before it ships (Principle 8).
14. **Regulatory considerations.** Minimal — internal knowledge is generally the least regulated
    content class, which is part of why this module is a good early build target.
15. **Additional guardrails.** None beyond the shared core.

## 3. AI Banking Assistant

1. **Mode.** Controlled — locked, no escalation path to Agentic (architecture §2.3).
2. **Data accessed.** Product/policy documents (via retrieval) plus live customer account data
   (via connector, never indexed — architecture §10.8's live/indexed distinction).
3. **Tools accessed.** `search_knowledge_base`, `get_account_balance`, `get_kyc_status`, and any
   other read-only CBS tool declared in that tenant's Connector Capability Manifest (Part 5 §5) —
   no write tools by default (architecture §10.8).
4. **Writes performed.** None in the MVP scope — a future increment may add transaction-intent
   creation for simple self-service actions, gated by the full Part 11 transaction architecture
   and step-up authentication (architecture §6.8), but this is explicitly out of MVP scope here.
5. **Roles permitted.** Authenticated customer only, scoped to their own `customer_scope`
   (Part 4 §1).
6. **Object-level restrictions.** Every account/customer reference is checked against
   `SecurityContext.customer_scope`, resolved from the bank's own system (Part 4 §2) — the
   central enforcement this module exists to prove works correctly under real conditions.
7. **Confidence threshold.** Tenant-set, informed by this being a customer-facing, brand-risk-
   sensitive surface — typically a stricter default than internal-knowledge modules.
8. **Escalation triggers.** Confidence below threshold, CoVe failure, or explicit customer request
   for a human — routed to the Customer Support queue (Human Escalation Service, Part 1 §2.26).
9. **Evidence requirements.** Citation for policy-derived claims; live data (account status) is
   presented with its own "as of" timestamp, never blended with retrieved policy text without
   clear attribution of which part came from which source (architecture §10.8's combined-answer
   pattern).
10. **Data leaving the tenant.** Same BYO-LLM rule; additionally, this module is the first to
    genuinely exercise the Customer Authentication Flow Engine (Part 1 §2.4) end-to-end.
11. **Model requirements.** Same call-site pattern; this module is the primary early proving
    ground for the full PII/Tokenization Service pipeline (Part 13) given the volume of
    account-number-bearing customer input it will receive.
12. **Failure modes.** CBS unavailable → "temporarily unavailable" (Part 1 §2.24's circuit
    breaker), never a guess about account status.
13. **Evaluation metrics.** A synthesized golden dataset covering realistic customer banking
    questions plus the full trust-and-safety adversarial suite specific to this module (account
    IDOR attempts, cross-customer leakage attempts — Part 22's test matrix).
14. **Regulatory considerations.** The highest-exposure module in the MVP set for consumer
    protection and data-privacy regulation (NDPA-equivalent, architecture §20) — the first module
    where a real compliance review (§20.2's honest caveat) is genuinely load-bearing before launch.
15. **Additional guardrails.** Step-up authentication readiness (even though writes are out of
    MVP scope, the authentication flow's step-up capability, architecture §6.8, must already exist
    for the future increment) — built now, exercised later.

## 4. Compliance Assistant

1. **Mode.** Controlled.
2. **Data accessed.** Regulatory/compliance policy documents, classified `compliance_security`
   or `regulatory` (architecture §12.2), plus AML/KYC case-relevant propositions where object-
   level ABAC (region, case assignment) restricts visibility (architecture §4).
3. **Tools accessed.** `search_knowledge_base`, scoped to compliance-classified content; no
   external connector required for the MVP scope of this module (a future increment may connect
   to a case-management system).
4. **Writes performed.** None.
5. **Roles permitted.** Compliance Officer role, with ABAC attributes (region, case assignment)
   further restricting which specific cases/documents are visible (architecture §4.2's worked
   example — two Compliance Officers, same role, different visibility).
6. **Object-level restrictions.** ABAC-driven — the module's own most important proving ground
   for the "same role, different permissions" requirement.
7. **Confidence threshold.** The strictest in the MVP set — compliance answers carry high
   regulatory stakes; the platform floor here is closest to binding (architecture §13.2).
8. **Escalation triggers.** Confidence below the (high) threshold, CoVe failure, or any query
   touching a case outside the officer's own ABAC scope (which should never surface as a partial
   answer — it should not surface at all, per Part 6 §3's pre-filter, so "escalation" here mostly
   applies to genuine confidence/evidence gaps, not scope violations, which are prevented
   structurally rather than escalated).
9. **Evidence requirements.** Strict citation to source regulatory text; this module is the
   primary early consumer of the Calculation Engine (Part 1 §2.21) for ratio/threshold
   computations common in compliance analysis.
10. **Data leaving the tenant.** Same BYO-LLM rule.
11. **Model requirements.** Same call-site pattern; a tenant may reasonably require a stronger
    model for this module given its stakes (Model Registry's per-tenant, per-call-site
    flexibility, Part 16 §3).
12. **Failure modes.** Same abstention-first posture as Document Intelligence, with a lower
    tolerance for proceeding on partial evidence given this module's stakes.
13. **Evaluation metrics.** A synthesized golden dataset built from realistic (not real, absent
    a specific bank's actual case data) AML/KYC compliance scenarios, per Principle 8.
14. **Regulatory considerations.** Directly regulatory-facing — this module's evaluation results
    are the most likely to be requested during an actual regulatory examination, making its audit
    trail (Part 14) and evidence citation quality the highest-priority correctness properties in
    the MVP set.
15. **Additional guardrails.** None beyond the shared core, applied at its strictest configured
    settings.

## 5. Regulatory Intelligence (first Agentic-mode module)

1. **Mode.** Agentic-allowed (architecture §2.3).
2. **Data accessed.** Internal regulatory/policy documents (via retrieval) plus, where a tenant
   enables it, external regulatory-feed connectors (a distinct connector type, still governed by
   the full Part 10 framework).
3. **Tools accessed.** `search_knowledge_base` (agent-chosen, potentially multiple calls per task,
   Part 9), plus any external regulatory-source connector explicitly enabled for this module —
   still subject to the same Connector Capability Manifest and object-level checks as any other
   tool (Part 9 §1, step [6]).
4. **Writes performed.** None in MVP scope.
5. **Roles permitted.** Compliance/Legal/Regulatory Affairs roles, tenant-defined.
6. **Object-level restrictions.** Same classification/ABAC filtering as Compliance Assistant,
   applied per tool call within the agent's trajectory (Part 9 §1, step [6]) rather than once at
   query time — this module is the primary proving ground for per-step authorization in the
   agentic lane.
7. **Confidence threshold.** Tenant-set; this module's output is explicitly framed as research
   assistance rather than a final compliance determination, which may justify a different
   threshold posture than Compliance Assistant's — a Business Decision Required per tenant.
8. **Escalation triggers.** Execution-budget breach (Part 9 §1) resulting in an incomplete task;
   confidence below threshold on the assembled result; any proposed tool call the agent cannot get
   authorized within its retry budget (Part 9 §2).
9. **Evidence requirements.** Every claim in the final synthesized result traces to a specific
   retrieved proposition or external-source citation, same citation discipline as the Controlled
   lane — Agentic mode changes orchestration, not the faithfulness requirement.
10. **Data leaving the tenant.** Same BYO-LLM rule; additionally, any external regulatory-feed
    connector's data handling is itself subject to the full Connector Framework (Part 10),
    including whatever encryption/auth that external source requires.
11. **Model requirements.** Agentic-mode tasks may reasonably use a different (often
    reasoning-capable) model than the Fixed pipeline's call sites — a distinct Model Registry
    entry for the Agent Orchestrator's planning call site, approved per tenant like any other.
12. **Failure modes.** Budget breach → explicit incomplete result (Part 9 §3), never presented as
    complete; stuck-agent watchdog termination → same incomplete-result handling.
13. **Evaluation metrics.** Agentic-specific metrics (architecture §16.5): task completion rate,
    trajectory correctness, tool-call count as a cost proxy — distinct from the single-turn
    exact-match metrics used elsewhere, since this module is not single-shot Q&A.
14. **Regulatory considerations.** Output must be clearly framed (in the UI and in the response
    itself) as research assistance requiring human judgment, not an autonomous regulatory
    determination — a product-framing requirement, not purely a technical one, but one the
    Confidence Engine and Human Escalation Service jointly support by making escalation the
    default posture for anything approaching a determination-level claim.
15. **Additional guardrails.** The full execution-budget enforcement of Part 9 is this module's
    single most safety-critical guardrail, more so than for any Controlled module, since this is
    the first place in the MVP set where the LLM genuinely chooses its own next step.

---

## Every Other Module — Deferred, Template Applies

Account Opening, KYC Guidance, Product Discovery, Customer Support, Complaint Assistance,
Financial Education (remaining Customer Experience); HR Assistant, IT Assistant, Operations
Assistant, Customer-Service Copilot, Training/Onboarding Assistant (remaining Employee
Experience); Credit Decision Support, AML/KYC Assistant, Risk Assistant, Fraud Investigation
Support (remaining Banking Operations); Executive Knowledge Assistant, Policy Intelligence,
Regulatory Impact Analysis, Operational Insights, Enterprise Search, Decision Support (remaining
Management) — each is specified using the identical fifteen-item template above, filled in when
that specific module is scheduled for build, not speculatively now (consistent with architecture
§23.5's explicit deferral).

---

*End of Part 21. Part 22 (Implementation Phasing) follows immediately.*
