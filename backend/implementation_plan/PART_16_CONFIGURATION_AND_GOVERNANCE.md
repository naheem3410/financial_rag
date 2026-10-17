# Master Technical Architecture and Implementation Plan
## Part 16 — Configuration & Governance

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §18 (model/prompt governance, change management)
and §22.3 (Plan Registry configurability). Builds on Part 1 §2.30 (Configuration Service) and
Part 5 §11 (`configuration_versions`).

---

## 1. One Generic Lifecycle, Many Domains

Every governed configuration domain — Models, Prompts, Guardrail parameters, Connector
definitions, Tenant policy (confidence thresholds, escalation rules, PII policy), Agent execution
limits, Plan Registry entries — shares the identical state machine, backed by the single
`configuration_versions` table (Part 5 §11):

```
DRAFT ──▶ VALIDATION ──▶ EVALUATION ──▶ APPROVAL ──▶ PRODUCTION ──▶ ROLLBACK (to any prior
  │           │              │             │             │           PRODUCTION version)
  │           │              │             │             │
  │           ▼              │             │             │
  │      schema/structural    │             │             │
  │      check fails ──▶      │             │             │
  │      REJECTED             │             │             │
  │                           ▼             │             │
  │                    gate-suite fails ──▶  │             │
  │                    REJECTED              │             │
  │                                          ▼             │
  │                                   second-approver      │
  │                                   required for          │
  │                                   sensitive domains ──▶  │
  │                                   (Part 15 §A) if not    │
  │                                   granted: REJECTED      │
  │                                                          ▼
  │                                                    canary rollout
  │                                                    (small traffic
  │                                                     slice / one
  │                                                     pilot tenant)
  │                                                    before full
  │                                                    PRODUCTION
  ▼
(a rejected draft is retained for reference, never silently discarded, so a repeated failed
 attempt is traceable)
```

## 2. Stage Definitions

- **DRAFT.** A proposed new version of a domain's configuration, created by an authorized actor
  (Platform engineering for base models/prompts; Bank Admin for tenant-scoped policy within their
  granted permission area, Part 15 §A).
- **VALIDATION.** Structural/schema checks specific to the domain — e.g. a confidence-threshold
  draft is rejected here if it falls below the platform floor (architecture §13.2); a connector
  draft is rejected here if it references an unregistered auth/encryption plugin (Part 10 §2).
- **EVALUATION.** For domains where behavior is measurable (models, prompts, guardrail
  parameters, agent limits) — a mandatory pass through the relevant slice of the evaluation gate
  suite (Part 23) before promotion can proceed. A domain with no measurable behavior (e.g. a
  cosmetic admin-portal label) skips this stage by definition, not by exception.
- **APPROVAL.** Sensitive domains require the second-approver rule (Part 15 §A) — approver
  distinct from proposer, enforced as a database constraint in addition to application logic.
- **PRODUCTION, via canary.** Nothing reaches every tenant simultaneously. A new version rolls
  out to a small traffic slice or a single pilot tenant first; only after a defined observation
  window with no regression signal does it promote to full production (architecture §18.3).
- **ROLLBACK.** Every prior PRODUCTION version remains retrievable; rollback is a single operation
  that reactivates a previous version's content — not a manual reconstruction from history.

## 3. Model Governance, Concretely

```python
class ModelVersionApproval(BaseModel):
    model_version_id: str
    tenant_id: str            # per-tenant approval — a bank can refuse a new model version
    approved_by: str
    approved_at: datetime
    evaluation_results_ref: str   # gate-suite results this approval was based on
```

- A provider silently swapping model weights under an unchanged model name is **treated as a new
  version requiring re-approval** — detected by pinning to an exact version string wherever the
  provider exposes one, and, where a provider does not expose a stable version identifier at all,
  by periodic canary comparison of output characteristics against a fixed regression set as an
  early-warning signal (an implementation detail flagged in Part 25 as needing engineering
  judgment, since no universal technical guarantee exists against an opaque provider-side change).
- **Bank-hosted local models (on-prem/dedicated, e.g. an Ollama-served open-weights model,
  architecture §11.6) have no external provider to silently swap anything — the bank controls the
  weights directly, which is a different risk shape, not an exemption from pinning discipline.**
  The same "exact version pinned" rule applies, verified by a **cryptographic hash check of the
  model artifact at load time**, compared against the hash recorded in the approved
  `model_versions` row, rather than by any provider-side version string (which does not exist for
  a self-hosted model). A hash mismatch at load time is treated identically to an unapproved
  model version — the LLM Gateway refuses the call (Part 1 §2.16's "no fallback to an unapproved
  default" rule) rather than proceeding against silently-changed local weights.
- `GET /models/{call_site}/{tenant_id}` resolves the currently approved, production version for
  that tenant — the LLM Gateway (Part 1 §2.16) calls this before every request; there is no
  fallback to an unapproved default.

## 4. Prompt Governance, Concretely

- Base prompts are versioned platform artifacts (Part 5 §11, `prompts`/`prompt_versions`).
- Tenant customization writes only to `prompt_versions.tenant_extensions` — a structurally
  separate field from `content` (the base prompt body). The LLM Gateway concatenates
  `content + tenant_extensions` at call time; **there is no code path by which a tenant extension
  can overwrite or precede the base prompt's structural instructions** (numeric-verbatim guard
  language, identity-never-claimed framing), since those instructions are enforced by the
  Guardrails component in code (Part 1 §2.19), not solely by prompt wording — even a maximally
  adversarial tenant extension cannot disable a check that doesn't live in the prompt at all.
- Every production response records `{prompt_version_id, model_version_id,
  configuration_version_id}` as part of its audit record (Part 14) — this is the concrete
  mechanism behind architecture §18.2's "every production response must identify the relevant
  versions."

## 5. Plan Registry Configurability (architecture §22.3)

The Plan Registry (Part 1 §2.42) uses this exact same lifecycle, with one distinction: it has no
EVALUATION stage (pricing has no gate-suite to pass), and its APPROVAL stage is Platform Admin
Console only, single-approver (pricing changes are a business decision, not a security-sensitive
technical change requiring the two-person rule — though a vendor may choose to apply it anyway).
This is what makes "the vendor can add, rename, reprice, or retire any plan element at any time
with no engineering release required" concretely true: a `plans` row's DRAFT → PRODUCTION
transition is a data change through this lifecycle, never a code deployment.

## 6. Change Management for Non-Configuration Changes (Retriever, Reranker, Embedding Model)

Code-level changes to the Retriever, Reranker, or Embedding Model swap (architecture §18.3)
follow the identical DRAFT → VALIDATION → EVALUATION → APPROVAL → PRODUCTION(canary) → ROLLBACK
shape, except the "version" being promoted is a deployed code artifact rather than a database row
— the EVALUATION stage's gate-suite pass is the same requirement, and the canary/rollback
mechanism is implemented at the deployment-orchestration layer (Part 2) rather than the
Configuration Service, but the governing rule is identical: nothing reaches every tenant at once,
and every promotion is preceded by a mandatory evaluation pass.

---

*End of Part 16. Part 17 (Security Architecture) follows immediately.*
