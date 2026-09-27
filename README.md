# mcp-obsidian-bridge

An MCP bridge connecting AI clients to Obsidian. It includes a shared
TLS-verified client and a restricted ChatGPT gateway used with OpenAI Secure MCP
Tunnel, plus diagnostic logging and deployment tooling.

This is a personal-use fork; general-purpose support is not provided.
The source repository is named `mcp-obsidian-bridge`. Existing runtime directories
retain their deployment names. The shared Python distribution and command retain
the upstream name `mcp-obsidian`; the import package remains `mcp_obsidian`.

## Layout

- `src/mcp_obsidian/`: TLS-verified client and MCP tools, used by multiple clients.
- `tests/`: shared client regression tests, including TLS verification.
- `chatgpt/`: ChatGPT gateway, path policy, existing deployment launcher, key-entry helper, and gateway tests.
- `docs/upstream/README.md`: original upstream documentation; its examples are historical, not deployment instructions for the restricted gateway.
- `docs/investigations/`: incident findings and outstanding fixes.
- `LOCAL_TLS_PATCH.md`: local TLS patch provenance.

This repository is maintained as a fork of
[MarkusPfundstein/mcp-obsidian](https://github.com/MarkusPfundstein/mcp-obsidian).
The original local import used commit `32285e9ac07049a8a23ea7d7903603a3e48a1bf7`;
the fork migration incorporates upstream history through
`5ee0b84fa8319fd2fdf0db0ee1febb065e712a15` and preserves the original local
commits, including deployed release IDs. Its MIT license is retained in `LICENSE`.
See [fork maintenance](docs/upstream/maintenance.md) for the downstream changes,
upstream update procedure, and migration verification.

## Development

Python 3.11+ and uv are required. From this repository:

```sh
uv sync --locked --group dev
uv run --locked pytest
```

The project pins MCP 1.29.0 and requests 2.34.2 to match the deployed launcher and explicitly declares the gateway's jsonschema dependency. The original client's stale lock (MCP 1.1.0 / requests 2.32.3) is replaced with a lock resolved for this project.

Tests use mocks, temporary files, and local HTTP/HTTPS fixtures. They do not write to the real Vault. They verify policy and client behavior; passing tests do not establish compatibility with every deployed REST API version.

## Deployment status

This checkout is the source repository. Runtime installations remain at:

- `~/.local/share/mcp-obsidian-tls/`
- `~/.local/share/obsidian-chatgpt-mcp/`

On 2026-09-22 the ChatGPT runtime was switched to release `1b5b818`, with a
dedicated environment under `releases/` and a managed `current` pointer. The
shared installation used by other clients remains unchanged. See the
[deployment verification record](docs/deployments/2026-09-22.md).

`chatgpt/launch.sh` is the historical launcher snapshot; deployment generates
the managed runtime launcher. Copying that historical file is not a deployment.

Credentials, trusted CA certificates, downloaded tunnel binaries, profiles, logs, and PID files stay outside Git. `chatgpt/connection.example.json` shows the configuration shape with placeholders. Never copy live `connection.json` or runtime keys into tracked files.

Use [the deployment runbook](docs/deployment.md) to prepare an isolated,
commit-addressed ChatGPT release, activate it, or restore the previous version.
Editing this checkout does not update the running service. The ChatGPT release
has its own locked environment; the other clients' shared installation stays separate.

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

The subsequent production deployment passed 272 tests, the installed-launcher
probe, and a Recent Changes call through the connected Obsidian app/Tunnel.
The remote call was correlated with a successful diagnostic event from the
Tunnel's gateway process; see the deployment record above.

Content-free diagnostic logging is implemented in the gateway, with bounded
file retention under the managed launcher; see [the diagnostics runbook](docs/diagnostics.md).
Check `scripts/deploy.py status` for the locally active release. Local tests
and source changes alone do not establish a successful production cutover.
