# Master Technical Architecture and Implementation Plan
## Part 2 — Deployment Architecture

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §4.5, §5, §22.10. Three deployment models,
one codebase, differing only by configuration (Principle 6, Part 1).

---

## A. Multi-Tenant SaaS

```
                                        INTERNET
                                            │
                                   ┌────────▼────────┐
                                   │   DNS / WAF /    │
                                   │       CDN        │
                                   └────────┬────────┘
────────────────────────────────────────────┼──────────────────────── VPC boundary
                                   ┌────────▼────────┐
                        PUBLIC     │  Load Balancer   │
                        SUBNET     │  (TLS terminate) │
                                   └────────┬────────┘
────────────────────────────────────────────┼──────────────────────── public/private split
                                   ┌────────▼────────┐
                                   │   API Gateway    │  (Part 1 §2.2)
                                   └────────┬────────┘
                                            │
                PRIVATE SUBNET  ┌───────────┼───────────────────────────┐
                                │           │                           │
                     ┌──────────▼──────┐ ┌──▼──────────────┐ ┌──────────▼──────────┐
                     │  API Services    │ │  Async Workers   │ │  Agent Orchestrator  │
                     │ (stateless, auto │ │ (ingestion, eval, │ │  (stateless workers)  │
                     │  scaled)         │ │  escalation retry)│ │                       │
                     └──────────┬──────┘ └──┬──────────────┘ └──────────┬──────────┘
                                │            │                           │
                     ┌──────────▼────────────▼───────────────────────────▼──────────┐
                     │                    Queue / Event Bus                          │
                     └──────────┬───────────────────────────────────────────┬───────┘
                                │                                           │
                PRIVATE SUBNET  │                                           │
       (data tier)   ┌──────────▼──────────┐   ┌─────────────┐   ┌─────────▼─────────┐
                     │  Relational Database │   │ Vector DB    │   │  Object Storage    │
                     │  (RLS, per-tenant    │   │ (per-tenant  │   │  (per-tenant       │
                     │   row isolation)     │   │  namespaces) │   │   prefixes)        │
                     └─────────────────────┘   └─────────────┘   └───────────────────┘
                                │                                           │
                     ┌──────────▼──────────┐                    ┌──────────▼──────────┐
                     │       Cache          │                    │  Secrets Manager     │
                     │ (tenant-keyed)       │                    │ (tenant namespaces + │
                     │                       │                    │  platform-global     │
                     │                       │                    │  license-signing key)│
                     └─────────────────────┘                    └─────────────────────┘
                                │
                     ┌──────────▼──────────┐               ┌────────────────────────┐
                     │  Observability       │               │  Backup / DR            │
                     │  (Langfuse + infra   │               │  (tenant-partitioned     │
                     │   metrics, redacted  │               │   snapshots)             │
                     │   before export)     │               └────────────────────────┘
                     └─────────────────────┘

────────────────────────────────────────────────────────────────────── VPC egress boundary
                                            │
                                   ┌────────▼────────┐
                                   │   Egress / NAT   │
                                   └────────┬────────┘
                                            │
                        ┌───────────────────┼───────────────────┐
                        ▼                   ▼                   ▼
              Bank's LLM Provider   Bank's CBS/Middleware   Bank's SMS/OTP Provider
              (tenant's own key,     (via Connector          (tenant's own account)
               OpenAI/Anthropic/     Framework, over
               local endpoint)       VPN/PrivateLink/
                                      IP allowlist per
                                      tenant config)
```

### Shared vs. tenant-isolated resources

| Resource | Shared across tenants? | Isolation mechanism |
|---|---|---|
| Load balancer, API Gateway, Queue infrastructure, Observability infrastructure | Yes — physically shared | Every request/message/log entry carries `tenant_id`; no cross-tenant read path exists in any service built on top |
| API service / worker / agent orchestrator compute | Yes — physically shared, horizontally scaled | Stateless; per-tenant concurrency/rate limits at the gateway (architecture §5.4, resource isolation) prevent one tenant starving another |
| Relational Database | Shared cluster, isolated at the row level | Row-Level Security policies keyed to the connection's resolved tenant context (architecture §4.4) |
| Vector Database | Shared cluster, isolated at the namespace level | Physically/logically separate namespace per tenant, never a single collection with a filter field |
| Object Storage | Shared bucket/account, isolated at the prefix level | Separate prefix (or bucket, backend-dependent) per tenant |
| Cache | Shared cluster | Tenant is part of every cache key (architecture §14.5) |
| Secrets Manager | Shared service, isolated at the namespace level | Tenant-scoped secret namespaces; the license-signing key is the one platform-global secret, held with the strictest access policy in the system |
| MCP server instances | **Not shared** | Deployed per tenant (architecture §2.4/§10.11) — a compromise of one is contained to one tenant |
| Backups | Shared backup infrastructure | Tenant-partitioned snapshots; one tenant's restore cannot touch another's data (architecture §19.3) |

### Egress-only posture

Outbound connections to a bank's LLM provider, CBS/middleware, and SMS/OTP provider originate from
the platform's egress; the platform never requires inbound connectivity from a bank's network for
the SaaS model (inbound-avoidance becomes mandatory only for on-prem/dedicated with strict network
requirements, §C below, but is good practice here too).

---

## B. Dedicated Tenant

**What changes from SaaS:** isolation moves from logical (namespace/row-level, shared compute) to
physical, for some or all layers, while the **application architecture is identical** — no code
path differs between SaaS and Dedicated.

```
                              SAME API Gateway / Load Balancer pattern as SaaS
                                                    │
                    ┌───────────────────────────────┴───────────────────────────────┐
                    │              DEDICATED ENVIRONMENT — BANK A ONLY               │
                    │  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐ │
                    │  │  API Services /  │  │  Relational DB / │  │  Vector DB /    │ │
                    │  │  Workers/Agents  │  │  Object Storage  │  │  Cache          │ │
                    │  │  (dedicated      │  │  (dedicated      │  │  (dedicated     │ │
                    │  │   compute pool)  │  │   instance)      │  │   instance)     │ │
                    │  └─────────────────┘  └─────────────────┘  └────────────────┘ │
                    │  ┌─────────────────┐  ┌─────────────────┐                     │
                    │  │  Secrets Manager │  │  MCP servers      │                     │
                    │  │  (dedicated      │  │  (already always  │                     │
                    │  │   namespace/     │  │   per-tenant,      │                     │
                    │  │   instance)      │  │   unchanged)       │                     │
                    │  └─────────────────┘  └─────────────────┘                     │
                    └─────────────────────────────────────────────────────────────┘
```

- **Whose cloud account.** May run in the platform vendor's cloud account *or* the bank's own
  cloud account, per contract (architecture §22.10) — the distinguishing factor from SaaS is
  physical isolation, not whose billing account it sits under.
- **Network.** Typically its own VPC/VNet, connected to the bank's network via VPN, PrivateLink,
  or IP-allowlisted peering, per that bank's requirement (architecture §15.4).
- **Observability and backup.** Remain logically per-tenant even here — no change to how they're
  scoped, only to how physically separated the underlying compute/storage is.
- **Update mechanism.** Vendor-managed rolling deployment, same release pipeline as SaaS
  (Change Management, Part 16), just applied to one tenant's dedicated environment on its own
  schedule if the contract requires deferred/staged updates for that bank specifically.

---

## C. On-Premises

**Definition (architecture §22.10, restated as the deployment diagram):** the entire platform —
application layer, data layer, and connector framework — deploys inside the bank's own
infrastructure. This is not the vendor collecting connection details while hosting the platform
elsewhere.

```
                                    BANK'S OWN NETWORK
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                            │
│   ┌──────────────────────────────── DMZ ────────────────────────────────┐                │
│   │   ┌─────────────┐        ┌──────────────────┐                       │                │
│   │   │ Reverse      │        │  API Gateway /    │                       │                │
│   │   │ Proxy / WAF  │───────▶│  Load Balancer    │                       │                │
│   │   └─────────────┘        └─────────┬────────┘                       │                │
│   └───────────────────────────────────┼────────────────────────────────┘                │
│                                        │                                                  │
│   ┌──────────────────────── APPLICATION TIER (internal network) ──────────────────────┐   │
│   │  ┌────────────────┐  ┌───────────────────┐  ┌────────────────────┐               │   │
│   │  │ API Services /  │  │  Agent Orchestrator│  │  Async Workers /    │               │   │
│   │  │ RAG Orchestration│  │  (if Agentic-mode  │  │  Ingestion Pipeline │               │   │
│   │  │                 │  │   modules licensed)│  │                     │               │   │
│   │  └────────────────┘  └───────────────────┘  └────────────────────┘               │   │
│   └──────────────────────────────────┬─────────────────────────────────────────────────┘   │
│                                       │                                                      │
│   ┌──────────────────────── DATA TIER (internal network) ────────────────────────────────┐  │
│   │  ┌───────────────┐  ┌─────────────┐  ┌────────────────┐  ┌────────────────────────┐ │  │
│   │  │ Relational DB  │  │  Vector DB   │  │ Object Storage  │  │  Bank's Secrets Manager │ │  │
│   │  │ (bank-hosted   │  │  (bank-hosted│  │  (bank-hosted   │  │  (BYOK — platform reads │ │  │
│   │  │  or vendor-    │  │   or vendor- │  │   or vendor-    │  │   references, never     │ │  │
│   │  │  supplied      │  │   supplied   │  │   supplied      │  │   holds raw keys)       │ │  │
│   │  │  container)    │  │   container) │  │   container)    │  │                         │ │  │
│   │  └───────────────┘  └─────────────┘  └────────────────┘  └────────────────────────┘ │  │
│   └───────────────────────────────────────────────────────────────────────────────────────┘  │
│                                       │                                                      │
│   ┌────────────────────── BANK'S EXISTING SYSTEMS ────────────────────────────────────────┐  │
│   │  ┌──────────────┐   ┌──────────────────┐   ┌─────────────────┐                       │  │
│   │  │ Bank's IAM /  │   │  CBS / Bank        │   │  Local LLM        │                       │  │
│   │  │ IdP (Azure AD/│   │  Middleware        │   │  Endpoint (if      │                       │  │
│   │  │ Okta/SAML)    │   │  (Connector        │   │  bank chooses      │                       │  │
│   │  │               │   │   Framework talks  │   │  self-hosted       │                       │  │
│   │  │               │   │   only to this)    │   │  model)            │                       │  │
│   │  └──────────────┘   └──────────────────┘   └─────────────────┘                       │  │
│   └───────────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                            │
│   ┌──────────────────────── OPS TIER ─────────────────────────────────────────────────────┐  │
│   │  ┌──────────────────┐   ┌───────────────────┐                                          │  │
│   │  │ Monitoring        │   │  Backup / Restore   │                                          │  │
│   │  │ (local stack, or   │   │  (bank-controlled,  │                                          │  │
│   │  │  Langfuse           │   │   RPO/RTO per       │                                          │  │
│   │  │  self-hosted)       │   │   architecture §19) │                                          │  │
│   │  └──────────────────┘   └───────────────────┘                                          │  │
│   └───────────────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
                    Only for deployments permitting outbound connectivity:
                                        ▼
                          License-validation heartbeat only
                          (to the platform vendor's Licensing
                           Service, architecture §22.9 — never
                           customer data, only a signature check)
```

### On-prem specifics

- **External LLM connectivity is optional, not assumed.** A bank may point the LLM Gateway at an
  external provider (their own API key, egress from their network) *or* at a fully local model
  endpoint inside their own network with zero external LLM traffic (architecture §11.6 of the
  original review, §22.1).
- **CBS/middleware connectivity never requires inbound access into the bank.** The Connector
  Framework, running inside the bank's own network, calls out to the CBS/middleware which is
  already reachable from inside that network — no reverse tunnel or inbound port opened
  specifically for the platform (architecture §15.5).
- **Update mechanism.** Delivered as the deployment package defined in architecture §22.10 —
  container images, deployment charts, configuration schemas, connector packages, migration
  scripts, the signed license, deployment documentation. The bank's own infrastructure team
  applies updates inside their approved change window, with vendor implementation assistance;
  updates are never pushed automatically into a bank's network without that bank's own change
  control.
- **License validation.** Online heartbeat where the bank's network policy permits any outbound
  connectivity (license-check payload only, never customer data); offline signed-file renewal
  process for fully air-gapped deployments (architecture §22.9).
- **Secrets and encryption keys.** BYOK is available here specifically — the bank's own Secrets
  Manager and encryption keys, with the platform holding only references (architecture §8.2).
- **Monitoring.** May run entirely on the bank's own observability stack, or a self-hosted Langfuse
  instance, with the same redaction-before-export rule applying regardless of which stack is in
  use (architecture §15.1).

---

## Deployment Decision Table

| Concern | SaaS | Dedicated | On-Prem |
|---|---|---|---|
| Application code | Identical | Identical | Identical |
| Compute isolation | Logical (shared, per-request tenant scoping) | Physical (dedicated pool) | Physical (bank's own hardware) |
| Data isolation | Logical (RLS/namespace/prefix) | Physical (dedicated instances) | Physical (bank's own hardware/containers) |
| Whose cloud account | Vendor's | Vendor's or bank's, per contract | N/A — bank's own data center |
| Network path to bank systems | Egress-only from vendor's VPC | Egress-only, or VPN/PrivateLink per contract | Internal network — no cross-boundary hop needed |
| License enforcement | Fully technical, vendor-controlled runtime | Fully technical, vendor-controlled runtime | Signed entitlement + heartbeat/offline renewal + contractual backstop (architecture §22.9) |
| Update delivery | Continuous, vendor-managed | Vendor-managed, tenant-scheduled | Deployment package, bank's own change control |
| Encryption key ownership | Vendor-managed by default | Vendor- or bank-managed per contract | BYOK available |

---

*End of Part 2. Part 3 (Trust Boundaries) follows immediately.*
