# Master Technical Architecture and Implementation Plan
## Part 10 — MCP / Connector Architecture

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §10 (the eleven-dimension connector framework) as
concrete interfaces. Builds on Part 1 §2.23 (Tool/MCP Gateway) and §2.24 (Connector Framework).

---

## 1. The Generic Connector Interface

```python
class ConnectorProfile(BaseModel):
    connector_id: str
    tenant_id: str
    connector_type: Literal["cbs", "hr", "crm", "fraud", "credit_bureau",
                             "transaction_monitoring", "auth_flow_step"]
    transport: TransportConfig
    authentication: AuthenticationConfig
    authorization: AuthorizationConfig
    credential_acquisition: CredentialAcquisitionConfig
    request_binding: RequestBindingConfig
    headers: HeaderConfig
    encryption: EncryptionConfig
    request_format: Literal["json", "xml", "soap_envelope", "graphql", "protobuf"]
    session_lifecycle: SessionLifecycleConfig
    request_lifecycle: RequestLifecycleConfig
    response_handling: ResponseHandlingConfig
    capability_manifest: CapabilityManifest
    version: int
    status: Literal["draft", "tested", "active", "disabled"]
```

Each nested config below is a **plugin-selected** structure — the field selects a named strategy
from a registry (§2), and the remaining fields are that strategy's own parameters, validated
against a strategy-specific schema.

### 1.1 Transport

```python
class TransportConfig(BaseModel):
    kind: Literal["rest", "soap_xml", "graphql", "grpc", "webhook", "bank_middleware"]
    base_endpoint: str                 # or a secret_reference if the endpoint itself is sensitive
    timeout_seconds: int = 30
    # gRPC-specific:
    proto_definition_ref: str | None = None
    # SOAP-specific:
    wsdl_ref: str | None = None
```

### 1.2 Authentication

```python
class AuthenticationConfig(BaseModel):
    strategy: Literal["basic", "api_key", "oauth2_client_credentials",
                       "oauth2_authorization_code", "oauth2_refresh_token", "jwt", "mtls",
                       "hmac", "ws_security", "custom"]
    custom_plugin_id: str | None = None   # required and security-reviewed if strategy == "custom"
    credential_bundle_ref: str            # points into connector_credentials (Part 5 §5)
```

### 1.3 Authorization (what the credential grants — distinct from Authentication)

```python
class AuthorizationConfig(BaseModel):
    scheme: Literal["bearer_token", "oauth_scopes", "roles", "permissions", "claims", "custom"]
    scopes_or_claims: list[str] = []
```

### 1.4 Credential Acquisition

```python
class CredentialAcquisitionConfig(BaseModel):
    method: Literal["static_secret", "login_endpoint", "token_endpoint", "token_refresh",
                     "certificate", "key_pair", "custom_handshake"]
    endpoint: str | None = None            # for login_endpoint / token_endpoint methods
    custom_handshake_plugin_id: str | None = None
```

### 1.5 Request Binding

```python
class RequestBindingConfig(BaseModel):
    kind: Literal["http_header", "query_parameter", "form_field", "soap_header",
                   "soap_envelope", "grpc_metadata", "graphql_variable"]
    field_mappings: dict[str, str]   # logical field name -> transport-specific placement
```

**This is the deliberate separation named in the architecture document**: an `AuthenticationConfig`
produces a credential (a token, a certificate); `RequestBindingConfig` decides how that credential
— and any other header or metadata — is attached to this specific transport's request shape. A new
transport requires a new `RequestBindingConfig` entry, never a new `AuthenticationConfig` for
credentials that already exist.

### 1.6 Headers

```python
class HeaderConfig(BaseModel):
    static: dict[str, str] = {}              # e.g. X-Channel-ID: fixed value
    templated: dict[str, str] = {}            # e.g. X-Client-ID: "{tenant_config.client_id}"
    generated: list[Literal["idempotency_key", "request_id"]] = []
```

**`idempotency_key` generation rule.** When `"idempotency_key"` is present, the Connector Framework
generates the key **once per logical write request** (keyed to the originating `transaction_intents.
idempotency_key`, Part 5 §7 — not regenerated independently here) and reuses the identical value on
every retry of that same request. This is enforced by the Connector Framework reading the key from
the `transaction_intents` row rather than generating a fresh UUID on each call attempt.

### 1.7 Encryption

```python
class EncryptionConfig(BaseModel):
    transport: Literal["tls", "mtls"] = "tls"
    payload: Literal["none", "aes_gcm", "aes_cbc", "rsa", "hmac_signing", "jwe", "xml_encryption",
                      "custom"] = "none"
    key_ref: str | None = None
    iv_ref: str | None = None
    custom_plugin_id: str | None = None
```

### 1.8 Session Lifecycle

```python
class SessionLifecycleConfig(BaseModel):
    strategy: Literal["stateless", "cached_token_with_refresh", "custom_state_machine"]
    token_ttl_seconds: int | None = None
    custom_state_machine_plugin_id: str | None = None
```

### 1.9 Request Lifecycle

```python
class RequestLifecycleConfig(BaseModel):
    timeout_seconds: int = 30
    max_retries: int = 2
    backoff_strategy: Literal["exponential", "fixed"] = "exponential"
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_reset_seconds: int = 60
```

### 1.10 Response Handling

```python
class ResponseHandlingConfig(BaseModel):
    parser: Literal["json", "xml", "soap", "protobuf"]
    error_mapping: dict[str, str]        # provider error code -> normalized platform error
    response_schema_ref: str             # the Pydantic contract every response is validated
                                          # against BEFORE being trusted (architecture §8.4)
```

### 1.11 Capability Manifest

```python
class CapabilityManifest(BaseModel):
    supported_operations: list[str]      # e.g. ["get_account_balance", "get_kyc_status"]
    writes_enabled: list[str] = []       # explicit allowlist; empty by default (read-only default,
                                          # architecture §10.8)
```

---

## 2. Plugin Registries (Auth and Encryption Strategies)

```python
class AuthStrategyPlugin(Protocol):
    def prepare_request(self, request: OutboundRequest, credentials: ResolvedCredentials
                         ) -> OutboundRequest: ...
    def refresh_if_needed(self, credentials: ResolvedCredentials) -> ResolvedCredentials: ...

class EncryptionStrategyPlugin(Protocol):
    def encrypt_payload(self, payload: bytes, key_ref: str, iv_ref: str | None) -> bytes: ...
    def decrypt_payload(self, ciphertext: bytes, key_ref: str, iv_ref: str | None) -> bytes: ...
```

Built-in implementations ship for every named strategy in §1.2/§1.7. A `custom` strategy requires:
(1) an implementation against the exact Protocol above, (2) a security review (architecture §6.2's
custom-auth review requirement, generalized here to custom encryption), (3) registration in the
plugin registry with a `plugin_id` referenced from `ConnectorProfile`, before it can be selected by
any tenant's connector configuration.

---

## 3. The MCP Gateway

The MCP Gateway is the Tool/MCP Gateway component from Part 1 §2.23, specified here at the level
of its exact call contract:

```python
class ToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    security_context: SecurityContext     # Part 4 — never client-supplied, always the request's
                                           # own immutable context

class ToolCallResult(BaseModel):
    status: Literal["success", "denied", "invalid_response", "unavailable"]
    data: dict[str, Any] | None
    denial_reason: str | None

def invoke(request: ToolCallRequest) -> ToolCallResult:
    # 1. Look up `tool_permissions` for request.security_context.roles — deny if absent.
    # 2. Look up the connector's `capability_manifest` — deny if tool_name/operation not listed.
    # 3. Object-level check: does request.arguments (e.g. account_reference) fall within
    #    request.security_context.customer_scope / abac_attributes? Deny if not
    #    (Part 4 §4's enforcement table — this is its concrete implementation).
    # 4. Resolve credentials via Secrets Management (never inline).
    # 5. Dispatch to Connector Framework using the tenant's ConnectorProfile.
    # 6. Validate the raw response against response_schema_ref BEFORE returning it.
    #    A response failing validation returns status="invalid_response", data=None —
    #    never a partially-trusted payload.
    # 7. Emit an audit_events row for this call (success, denial, or invalid_response alike).
    ...
```

**Steps 1–3 execute regardless of what the calling LLM or Agent Orchestrator "believes" is
authorized — this is the concrete mechanism behind "the tool enforces object-level authorization
independently of the LLM" (architecture §13.8).**

## 4. Bank Middleware Indirection — Configuration-Only Change

```
ConnectorProfile.transport.kind = "bank_middleware"
ConnectorProfile.transport.base_endpoint = "<bank's middleware endpoint>"
```

No other field of `ConnectorProfile`, and no field of `ToolCallRequest`/`ToolCallResult`, changes.
`capability_manifest.supported_operations` still lists `"get_account_balance"`; the Tool/MCP
Gateway's `invoke()` function is unchanged; only the Connector Framework's dispatch target differs.
This is the literal implementation of architecture §10.5/§15.1's "the MCP tool interface remains
identical."

## 5. Network-Level Restrictions

Where a bank requires VPN, PrivateLink, IP allowlisting, or an mTLS-terminated proxy in front of
their middleware (architecture §15.4), this is configured as network-layer infrastructure around
the Connector Framework's outbound call path (Part 2's deployment diagrams show the egress path
this applies to) — it does not change `ConnectorProfile` itself, since it is a network topology
concern, not a connector-behavior concern.

---

*End of Part 10. Part 11 (Transaction Architecture) follows immediately.*
