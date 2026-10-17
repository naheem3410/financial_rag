# Master Technical Architecture and Implementation Plan
## Part 13 — PII Architecture

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §7 in full technical detail. Builds on Part 1
§2.28 (PII/Tokenization Service).

---

## 1. Detection

Every inbound text surface — customer/employee query, uploaded document content (Part 12 §5's
extracted text), tool response payload — passes through PII detection **before** it reaches any
other component. Detection is a swappable interface:

```python
class PIIDetector(Protocol):
    def detect(self, text: str, tenant_policy: PIIPolicy) -> list[PIIMatch]: ...

class PIIMatch(BaseModel):
    category: str          # e.g. "account_number", "bvn", "private_person", "private_phone"
    span: tuple[int, int]
    value: str              # the raw matched value — exists only transiently, in memory,
                             # for the duration of the redact() call below
```

Built-in adapters: a self-hostable detector (e.g. Microsoft Presidio), a bank-specific
deterministic regex set (BVN, NIN, account/card number formats), and optionally a
vendor-provided detection model — **the exact adapter and its precise category set must be
verified against current vendor documentation immediately before implementation** (this document
does not assert the capabilities of any specific third-party product as final).

## 2. Tokenization

```python
def redact(text: str, tenant_policy: PIIPolicy, session_id: str) -> tuple[str, MappingHandle]:
    matches = detector.detect(text, tenant_policy)
    tokenized_text = text
    mapping_handle = MappingHandle(session_id=session_id)
    for match in matches:
        if match.category in tenant_policy.categories_to_redact:
            token = allocate_token(match.category)     # e.g. "<ACCOUNT_1>"
            mapping_handle.add(token, match.value)      # stored ONLY in the vault, §3
            tokenized_text = tokenized_text.replace(match.value, token)
    return tokenized_text, mapping_handle
```

**`match.value` — the raw PII — exists in process memory only for the duration of this function
call.** It is never returned, logged, or passed to any caller of `redact()` except as an opaque
`mapping_handle` reference.

## 3. The PII Vault

- **Storage.** A dedicated, encrypted store, separate from the general relational database,
  keyed by `mapping_handle` (itself scoped to `tenant_id + session_id`).
- **Contents.** `{token: raw_value}` pairs, encrypted at rest with a key held in Secrets
  Management (Part 1 §2.29).
- **Lifetime.** Session-scoped by default; deletion timeline follows the tenant's own configured
  retention policy for the `pii_vault` data class (Part 5 §10), **not a platform default** —
  jurisdictional requirements vary.
- **No durable copy of raw PII exists anywhere else in the platform.** Documents, conversations,
  logs, traces, and the embedding/index layer all hold only tokenized text.

## 4. Token Resolution — When Real PII Is Allowed to Exist

**This is the single most important rule in this part, stated exactly:**

```python
def resolve(token: str, mapping_handle: MappingHandle, security_context: SecurityContext,
            calling_connector_call: ConnectorCallContext) -> str:
    # Callable ONLY from within the Connector Framework, at the exact point of constructing an
    # outbound request to a bank system that genuinely requires the real value (e.g. a CBS
    # lookup needing the actual account number).
    #
    # NOT callable from:
    #   - the LLM Gateway (no such tool/function is exposed to any LLM call site)
    #   - the Agent Orchestrator's tool-proposal mechanism (an agent can request a TOKEN be
    #     used as a tool argument; it cannot request resolution of the token itself)
    #   - any logging, tracing, or audit code path
    #   - the Context Builder
    #
    # Authorization check: the resolution request must be tied to a specific, already-authorized
    # ToolCallRequest (Part 10 §3) — a bare "give me the real value for this token" call, with no
    # associated authorized connector call, is not a valid call shape and is rejected.
    ...
    real_value = vault.lookup(token, mapping_handle)
    return real_value   # exists in memory only for the duration of constructing and sending the
                          # outbound connector request; never persisted, never logged
```

**Real PII therefore exists, at any point in the platform's runtime, only:**
1. Transiently in memory during `redact()`'s detection pass (§2).
2. At rest, encrypted, inside the PII Vault (§3).
3. Transiently in memory during `resolve()`'s call, strictly inside a Connector Framework request
   construction, immediately serialized into the outbound request to the bank's own system and
   discarded from memory afterward.

**It never exists in: the LLM's prompt or context window, any log line, any trace span (Langfuse
or otherwise), the cache, the relational database's general tables, or any audit record.**

## 5. Boundaries, Enumerated

| Boundary | PII state at this boundary |
|---|---|
| Customer/employee input → Platform (Part 3 Boundary 2/3) | Redacted before any downstream service sees it |
| Document → Ingestion (Boundary 12) | Redacted as part of the extraction pipeline (Part 12 §5), before embedding |
| Application → LLM (Boundary 8) | Tokenized only — the LLM never receives a raw value |
| Application → MCP / Tool → External System (Boundary 9/10/13/14) | Tokenized in the tool-call proposal; resolved to raw only inside the Connector Framework's own request construction, immediately before transmission to the bank system, per §4 |
| Logging | Tokenized — the redaction pipeline runs before any log line is written, not after |
| Tracing (Langfuse) | Tokenized — redaction happens before export, per architecture §15.1, not as a post-processing filter on already-exported traces |

## 6. Malicious Resolution Attempts

Architecture §7.5 asks whether a malicious user can force the system to reveal original PII. Given
§4's constraint — `resolve()` has no caller outside an already-authorized Connector Framework
request — the answer is structural: there is no user-facing or LLM-facing API surface through
which a resolution request can be issued directly. A user asking the AI "what is the real account
number behind `<ACCOUNT_1>`" receives no answer, because no call site the LLM can invoke maps to
`resolve()` — the LLM can only ever *use* a token as an opaque value in a tool-call proposal, never
retrieve what it stands for.

## 7. Tenant Configuration

`PIIPolicy` (feeding `configuration_versions`, domain = `pii_policy`) is tenant-editable through
the Bank Admin Portal (architecture §17.1): which categories to redact, and the retention window
for the vault. Different banks, different rules, identical mechanism underneath (architecture
§7.4).

---

*End of Part 13. Part 14 (Audit Architecture) follows immediately.*
