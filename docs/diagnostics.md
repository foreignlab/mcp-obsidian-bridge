# Gateway diagnostics

The ChatGPT gateway writes one JSON record per line to **stderr**. Stdout
remains exclusively for MCP protocol messages. No extra dependency or logging
service is required. These changes are in the source checkout; deployment and
verification of the running Tunnel's log capture are still pending.

## What is recorded

Every record contains a UTC `timestamp`, a randomly generated `run_id`, the
process `pid`, and an `event`. Tool events also contain an internally generated
`call_id` and a tool name from the allowlist. Unrecognized names become
`unknown`; client-provided request IDs are not logged.

| Event | Meaning |
| --- | --- |
| `gateway_starting` | The gateway entered its startup checks. |
| `gateway_ready` | Configuration passed and stdio opened; the MCP handshake is not yet established by this event. |
| `mcp_initialized` | The client's MCP initialized notification was received. |
| `gateway_failed` | Startup or the serving loop failed; `stage` identifies the last step. The process exits with code 1. |
| `gateway_stopped` | The stream closed normally or the task was cancelled. |
| `tool_started` | A tool call entered the gateway, before argument and policy validation. |
| `api_error` | An individual REST operation failed. Recorded even if a batch or fallback catches the exception. |
| `tool_failed` | The tool raised an exception; includes a safe `category` and duration. |
| `tool_completed` | The handler returned; includes duration and API error count. |
| `library_warning` / `library_error` | A dependency logged a warning/error. Only the severity and coarse source (`mcp` or `other`) are retained. |

`duration_ms` uses a monotonic clock. Terminal tool records carry
`api_error_count`; `tool_completed.outcome` is `success` or
`completed_with_api_errors`. The latter may mean a partial batch result **or**
a successful fallback after a failed attempt. It does not assert that the whole
tool failed, nor that all individual operations succeeded.

Example using synthetic values:

```json
{"timestamp":"2026-09-22T05:00:00.000+00:00","run_id":"example-run","pid":1234,"event":"tool_failed","call_id":"example-call","tool":"obsidian_get_recent_changes","category":"api_http","http_status":400,"api_error_code":40012,"duration_ms":12.5,"api_error_count":1}
```

## Failure categories and stages

| Category | Check next |
| --- | --- |
| `tool_not_allowed` | The caller requested a tool outside the ChatGPT allowlist. |
| `invalid_arguments` | Tool arguments did not match the advertised schema. |
| `policy_rejected` | The request violated the Vault path/write policy. |
| `api_auth` | Obsidian REST API returned HTTP 401 or 403; this does not diagnose Tunnel authentication. |
| `api_http` | REST API returned another HTTP error. Numeric status and API error code are retained when available. |
| `api_tls` | Certificate or TLS verification failed. Check the trusted CA and certificate expiry. |
| `api_timeout` | A REST request timed out. |
| `api_connection` | A REST connection failed. Check Obsidian and the configured endpoint. |
| `api_request` | Another requests-library failure occurred. |
| `tool_error` | The tool failed without a recognized REST exception. Reproduce with synthetic inputs to investigate. |

Startup `stage` values are `load_config`, `https_config`, `ca_config`,
`vault_config`, `api_key_check`, `tls_check`, `create_gateway`, and `stdio`.
They identify where to investigate without printing configuration or paths.

## Investigating an incident

1. Record the user's failure time with timezone and the displayed error text.
   The diagnostic timestamps use UTC (subtract nine hours from JST).
2. Check Tunnel health and its forwarding log separately. Gateway diagnostics
   cannot establish why a request never reached this Mac.
3. In the gateway stderr capture, find the relevant `run_id`, then follow the
   matching `call_id` from `tool_started` to `tool_completed` or `tool_failed`.
4. Compare `api_error` records and numeric codes. A started call without a
   terminal record may indicate termination, a hang, or missing log output;
   it does not prove a particular cause.
5. If only `library_error` appears, correlate it with startup/initialization
   and Tunnel events. Raw dependency messages and tracebacks are deliberately
   omitted, so this event alone does not identify the exact protocol error.

The current LaunchAgent captures stderr at
`~/.local/share/obsidian-chatgpt-mcp/tunnel/launchd.stderr.log`; the Tunnel's own
runtime log is `tunnel/launchd-runtime.log`. During deployment, verify that the
new JSON records actually reach the stderr capture through the Tunnel.

Example searches after that verification:

```sh
log="$HOME/.local/share/obsidian-chatgpt-mcp/tunnel/launchd.stderr.log"
rg '"event":"(gateway_failed|tool_failed|api_error|library_error)"' "$log"
rg '"call_id":"REPLACE_WITH_CALL_ID"' "$log"
```

## Privacy and operational limits

- Arguments, note content, file/Vault paths, credentials, raw exception
  messages, response bodies, and tracebacks are not included in these records.
  API error codes are restricted to five-digit integers and HTTP status to
  integers from 100 through 599.
- Only the dedicated gateway entry point replaces Python logging handlers
  with a content-free handler. The shared client emits request observations
  only while a gateway tool call is active. Codex/Claude client logging
  configuration is not changed.
- Tool errors returned over MCP retain their existing behavior. This logging
  policy is not a redaction policy for tool responses or Tunnel-owned logs.
- A stderr write failure is ignored so diagnostics cannot turn a completed
  Vault write into an apparent failure and encourage a duplicate retry.
- Imports before entry into `main`, forced process termination (including
  SIGTERM/SIGKILL), and failures before reaching the gateway may have no
  terminal event. The absence of logs is not proof that no incident occurred.
- The gateway does not rotate or delete the enclosing LaunchAgent log.
  Configure retention/rotation as part of deployment; existing captured logs
  are not retroactively redacted.

## Verification

`chatgpt/tests/test_diagnostics.py` covers safe event fields, correlated calls,
schema/policy rejection through SDK dispatch, API status/code classification,
TLS/connection/timeout failures, partial batch results, observer cleanup,
stderr write failure, startup validation stages, and dependency-message
redaction. A subprocess test performs a real stdio MCP handshake and rejected
tool calls with synthetic secrets, verifying that protocol traffic and JSON
diagnostics remain separate.

Run the full suite with `.venv/bin/python -m pytest -q`. Tests use temporary
files and fixtures; they do not modify the real Vault.
