# obsidian-chatgpt-mcp

Shared Obsidian MCP client and the restricted ChatGPT gateway used with OpenAI Secure MCP Tunnel.

The source project is named `obsidian-chatgpt-mcp`, matching the deployed
gateway directory. The shared Python distribution and command retain the
upstream name `mcp-obsidian`; the import package remains `mcp_obsidian`.

## Layout

- `src/mcp_obsidian/`: TLS-verified client and MCP tools, used by multiple clients.
- `tests/`: shared client regression tests, including TLS verification.
- `chatgpt/`: ChatGPT gateway, path policy, existing deployment launcher, key-entry helper, and gateway tests.
- `docs/upstream/README.md`: original upstream documentation; its examples are historical, not deployment instructions for the restricted gateway.
- `docs/investigations/`: incident findings and outstanding fixes.
- `LOCAL_TLS_PATCH.md`: local TLS patch provenance.

The shared client derives from [MarkusPfundstein/mcp-obsidian](https://github.com/MarkusPfundstein/mcp-obsidian), commit `32285e9ac07049a8a23ea7d7903603a3e48a1bf7`. Its MIT license is retained in `LICENSE`. The local snapshot also includes existing functional changes and regression tests; it is not a pristine upstream checkout.

## Development

Python 3.11+ and uv are required. From this repository:

```sh
uv sync --locked --group dev
uv run --locked pytest
```

The project pins MCP 1.29.0 and requests 2.34.2 to match the deployed launcher and explicitly declares the gateway's jsonschema dependency. The original client's stale lock (MCP 1.1.0 / requests 2.32.3) is replaced with a lock resolved for this project.

Tests use mocks, temporary files, and local HTTP/HTTPS fixtures. They do not write to the real Vault. They verify policy and client behavior; passing tests do not establish compatibility with every deployed REST API version.

## Deployment status

This checkout is the source repository. The current running copies remain at:

- `~/.local/share/mcp-obsidian-tls/`
- `~/.local/share/obsidian-chatgpt-mcp/`

No running service or client configuration was changed by the initial import. `chatgpt/launch.sh` deliberately retains its existing absolute deployment paths; it starts the deployed copy, not this checkout. `chatgpt/README.md` documents that deployment.

Credentials, trusted CA certificates, downloaded tunnel binaries, profiles, logs, and PID files stay outside Git. `chatgpt/connection.example.json` shows the configuration shape with placeholders. Never copy live `connection.json` or runtime keys into tracked files.

Deployment automation and cutover are pending. Editing this checkout does not update the running service. Plan and validate deployment separately, including rollback, because Codex and Claude Code also use the shared client.

## Recent Changes compatibility

`obsidian_get_recent_changes` uses a fixed internal JSONLogic query supported by
Local REST API 5.1.0. It returns Markdown notes modified since local midnight
`days` calendar days ago, inclusive, using the MCP process's timezone. Run the
client in the same timezone as Obsidian to preserve the previous DQL boundary.
Results are sorted by modification time descending, then by filename for ties,
before applying `limit`.

The former table response shape is preserved:

```json
[{"filename": "example.md", "result": {"file.mtime": "2026-09-22T09:00:00.123+09:00"}}]
```

The query requests timestamps only; it does not return note content or expose
arbitrary JSONLogic through the ChatGPT gateway. TLS verification and existing
write restrictions remain enabled.

The source fix passed 224 tests and read-only HTTPS checks against the installed
REST API 5.1.0 on 2026-09-22, both directly and through the source gateway's tool
dispatcher. The checks were not made through ChatGPT or the running Tunnel.
See [the investigation and validation record](docs/investigations/2026-09-22.md).

Deployment is still pending: the running copies retain the DQL implementation.
Next: add redacted diagnostic logging, establish deployment/rollback steps, then
deploy and verify through ChatGPT.
