# UDS Model Context Protocol (MCP)

UDS exposes a **Model Context Protocol** surface so AI agents can inspect the
platform through a standard, read-only set of tools and resources. It speaks
plain JSON-RPC 2.0 over HTTP, so any MCP client that supports remote HTTP
servers can connect to it (Claude Code, Cursor, opencode, ...).

The surface is generated from the same REST handler inventory the REST API
and the OpenAPI generator use, so what an agent sees is exactly what the
broker can serve — and every call is executed **as the authenticating user**,
with the usual REST permission checks in force.

## Enabling the service

Configuration lives in the admin GUI (`Configuration` → `Security` section):

| Setting | Default | Meaning |
| --- | --- | --- |
| `MCP Enabled` | `false` | Master switch. When off, the endpoint answers `404` — indistinguishable from a non-existent path. |
| `MCP Rate Limit` | `600` | MCP requests per user and minute. `0` means unlimited. Notifications (fire-and-forget messages) consume the same budget and are silently dropped when it is exhausted. |

Two more behaviours to know:

- The MCP surface is restricted to **staff and administrator** identities:
  any other user gets `403` before reaching the protocol layer.
- It inherits the **admin origin policy** (`Admin trusted sources`
  configuration): requests from networks outside the trusted list are
  rejected with `403`. The default (`*`) imposes no restriction.

## Authentication: user API tokens

Agents authenticate exactly like any REST API consumer: a
`Authorization: Bearer <token>` header on every request.

Tokens are **user API tokens**:

- Prefix `uat-`, no expiry, and **one token per user** — issuing a new one
  requires revoking the previous first.
- Stored hashed: the raw value is shown once at creation, and only a short
  hint is kept (in the user's properties).
- The user keeps its normal REST permissions; the agent can never do more
  than the user could by hand.

Only administrators can issue or revoke a token, through the REST API:

```bash
# Issue (the raw token appears exactly once, in this answer)
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://uds.example.com/uds/rest/authenticators/<auth-uuid>/users/<user-uuid>/token"
# => {"user_uuid": "...", "token": "uat-...", "token_hint": "uat-abcd…wxyz"}

# Revoke
curl -s -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://uds.example.com/uds/rest/authenticators/<auth-uuid>/users/<user-uuid>/token"
```

Prefer issuing tokens to a dedicated staff account (or an administrator one
if the agent also needs the admin-only system log tool) instead of reusing a
personal account.

## The endpoint

Single URL, one HTTP verb:

```
POST https://uds.example.com/uds/rest/mcp
Content-Type: application/json
Authorization: Bearer uat-...
```

Protocol notes:

- JSON-RPC 2.0. Supported protocol revisions at handshake time:
  `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25` and `2026-07-28`
  (the current one). A client asking for an unknown revision gets the latest
  one back and decides whether to continue, as the specification prescribes.
- JSON-RPC **notifications** (messages without `id`) are acknowledged with
  an empty `202 Accepted`.
- `GET` and `DELETE` on the endpoint answer `405 Method Not Allowed` with an
  `Allow: POST` header: the server is stateless and does not offer the
  server-to-client SSE stream.
- Requests whose `Accept` header rules out `application/json` get `406 Not
  Acceptable`.
- `tools/list` and `resources/list` are **paginated**: follow the opaque
  `nextCursor` value until it is absent.

### Error codes

Domain errors never leak as plain REST errors; they always travel as JSON-RPC
error envelopes echoing the request `id`:

| Code | Meaning |
| --- | --- |
| `-32600` | Invalid request (malformed JSON-RPC body). |
| `-32601` | Method not implemented. |
| `-32602` | Invalid params (unknown tool, arguments failing the published schema). |
| `-32000` | Server error (rate limit exhausted, access denied, handler errors). |
| `-32002` | Resource not found (`resources/read`). |

HTTP level: `403` for non-staff identities or untrusted origins, `404` when
the service is disabled, `405` for `GET`/`DELETE`, `406` for incompatible
`Accept` headers.

## What the agent gets

Three kinds of capabilities, all read-only:

### Generated list tools

One `list_<collection>` tool per model collection (master collections and
their details — providers, services, service pools, users, groups, assigned
services, servers, tunnels, ...). Each accepts OData-style structured
arguments: `filter`, `orderby`, `top`, `skip` and `select`; tools of detail
collections additionally require `parent_uuid`.

For LLM-friendliness, pages are bounded even when the REST collection is
not: `top` defaults to 100 and is clamped to 500.

### Curated tools

Hand-crafted tools for the high-value read-only surfaces beyond plain
listings:

| Tool | Purpose |
| --- | --- |
| `get_servicepool_fallback_access` | Current fallback access policy of a service pool. |
| `get_metapool_fallback_access` | Same, for a meta pool. |
| `get_servicepool_forecast` | Usage forecast from the pool's historical weekly profile. |
| `get_servicepool_cache_recommendations` | Cache sizing recommendations for a pool. |
| `get_server_group_stats` | Aggregate per-server status, load and weights of a server group. |
| `get_server_stats` | Resource usage time-series (cpu, memory, users, connections, disk) for one server. |
| `search_authenticator` | Search users or groups of an authenticator by name. |
| `get_item_logs` | The log trail of one object: a service pool, a user, a service, a meta pool member or an assigned service. |
| `get_system_logs` | Global (system) log — administrators only (see below). |
| `get_platform_stats` | Historical usage series of the platform (assigned/inuse/cached/complete), platform-wide or per pool. |
| `get_security_check` | Security self-assessment findings — administrators only. |
| `report_failed_logins` | CSV aggregation of failed login attempts over a date range — administrators only. |
| `report_admin_activity` | CSV summary of administrator activity (requests, errors, top endpoints) — administrators only. |

### Resources

- `uds://system/overview` — aggregate platform counters.
- `uds://version` — running UDS version and build information.

### Sensitive data redaction

Before any result reaches the model, keys commonly carrying secrets
(`password`, `token`, `secret`, `cookie`, `certificate`, ...) are replaced
with the literal `REDACTED` — case-insensitively and recursively. This is a
defensive net: permissions already bound what the user can read.

## Global system log (administrators)

The global (system) log — the same lines that go to the journal and the
broker's log file — is served by an admin-only REST endpoint, and is the
backing of the `get_system_logs` MCP tool:

```
GET /uds/rest/logs
```

Parameters (all optional): `since` / `until` (ISO 8601 datetime, or a plain
date — start of day for `since`, end of day for `until`), `level` (minimum
severity name), `source` (exact, case-insensitive), `limit` (page size,
default 100, capped at 1000). Standard OData parameters are honoured too
(`$filter` applied at the query level, `$orderby`, `$skip`, `$top`), plus
the `X-Total-Count` response header and the RFC 10008 `QUERY` verb with the
parameters in the request body.

The answer is an object `{entries, truncated, limit, hint?}`: when more
entries matched the filters than the page allowed, `truncated` is `true` and
`hint` explains how to get the rest.

## The skill bundle

Staff users can download a ready-made **skill bundle** preconfigured for the
running broker:

```
GET /uds/rest/skill
```

The answer is a JSON envelope (`name`, `mime_type: application/gzip`,
`encoding: base64`, `size`, `sha256`, `data`) wrapping a tar.gz regenerated
on every request from the live catalog, so it always matches the running
broker. Contents:

- `uds-mcp/SKILL.md` — what the agent can access, generated from the catalog.
- `uds-mcp/mcp_config.json` — client configuration entry point (below).
- `uds-mcp/README.md` — installation steps.

The broker URL is baked into the bundle; the only value the user configures
is the `UDS_TOKEN` environment variable with a `uat-...` token.

## Client configuration

### Generic `mcpServers` clients (Claude Code, Cursor, ...)

The bundle's `mcp_config.json` contains a ready entry of the widely
supported `mcpServers` shape:

```json
{
  "mcpServers": {
    "uds": {
      "type": "http",
      "url": "https://uds.example.com/uds/rest/mcp",
      "headers": { "Authorization": "Bearer ${UDS_TOKEN}" }
    }
  }
}
```

### Other clients

Most MCP clients read the same `mcpServers` shape; only the configuration
file location differs. The bundle's `mcp_config.json` is a drop-in starting
point — replace `${UDS_TOKEN}` with the interpolation syntax your client
supports:

| Client | Configuration | Notes | Documentation |
| --- | --- | --- | --- |
| Cursor | `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) | `mcpServers` with `url` + `headers`; `${env:UDS_TOKEN}` interpolation | [cursor.com/docs/context/mcp](https://cursor.com/docs/context/mcp) |
| Claude Code | `.mcp.json` (project) or `~/.claude.json`, or `claude mcp add --transport http uds https://…/uds/rest/mcp --header "Authorization: Bearer $UDS_TOKEN"` | `${UDS_TOKEN}` expansion supported in `headers` | [code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp) |
| VS Code (Copilot) | `.vscode/mcp.json` (workspace) or the user-profile `mcp.json` | Wrapper key is `servers` (not `mcpServers`), entries with `"type": "http"`, `url` and `headers` | [code.visualstudio.com/docs/copilot/chat/mcp-servers](https://code.visualstudio.com/docs/copilot/chat/mcp-servers) |

For any other client, look for the standard `mcpServers` (or equivalent)
remote-server entry: a `url` pointing at `/uds/rest/mcp` plus an
`Authorization` header. Clients without header support for HTTP servers can
still connect by running a local bridge (e.g. `mcp-remote`) against the same
URL.

### opencode

opencode uses its own configuration schema (it does not read `mcpServers`),
so the entry is written by hand — typically disabled by default and enabled
only for UDS-related sessions:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "uds": {
      "type": "remote",
      "url": "https://uds.example.com/uds/rest/mcp",
      "enabled": false,
      "oauth": false,
      "timeout": 15000,
      "headers": { "Authorization": "Bearer {env:UDS_TOKEN}" }
    }
  }
}
```

Check connectivity with `opencode mcp debug uds` / `opencode mcp list`; the
tools appear prefixed with the server name (`uds_list_servicespools`, ...).

### Smoke test with curl

```bash
export UDS_TOKEN=uat-...
UDS=https://uds.example.com/uds/rest/mcp

# Handshake
curl -s -X POST "$UDS" -H "Authorization: Bearer $UDS_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-11-25","capabilities":{},
                 "clientInfo":{"name":"curl","version":"1.0"}}}'

# List tools (first page)
curl -s -X POST "$UDS" -H "Authorization: Bearer $UDS_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# Call a tool
curl -s -X POST "$UDS" -H "Authorization: Bearer $UDS_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
       "params":{"name":"list_servicespools","arguments":{"top":10}}}'
```

## Security model in one view

- **Read-only**: no tool mutates state; write operations are a separate,
  not-yet-shipped phase.
- **Staff gate + trusted origins + rate limit** (including notifications).
- **Run-as-user permissions** on every proxied call — the agent is bound to
  what its user may do.
- **Audit**: every `tools/call` and `resources/read` is recorded in the
  global syslog with user, operation, arguments and outcome.
- **Redaction** of sensitive keys before results reach the model.
- **Admin-only** global log endpoint and tool.

## Status and roadmap

- **Current**: read-only surface (generated listings + curated tools +
  resources), skill bundle, per-object and system logs, platform usage
  stats, security self-assessment and CSV analytical reports.
