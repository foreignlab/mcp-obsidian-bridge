# Deploying the ChatGPT gateway

Deployment is local and commit-addressed. The ChatGPT runtime has its own
virtual environment; the shared `~/.local/share/mcp-obsidian-tls` installation
used by other clients is not modified. Keep the working checkout available
for deployment and recovery commands.

For the shared stdio installation, use the separate
[shared deployment runbook](shared-deployment.md).

## Layout

Under `~/.local/share/obsidian-chatgpt-mcp/`:

| Path | Purpose |
| --- | --- |
| `releases/<full-commit>/` | Archived source, locked `.venv`, and `release.json` manifest. |
| `current` | Atomic symlink to the active release. |
| `launch.sh` | Stable command already referenced by the Tunnel profile. Resolves `current` once, then executes that release. |
| `connection.json` | Existing private configuration; releases link to it rather than copy credentials. |
| `deploy/legacy-launch.sh` | Original launcher retained for the first rollback. Original gateway/shared-client files remain untouched. |
| `deploy/state.json` | Active and previous release (or `legacy`). |
| `deploy/pending.json`, `pending-launch.sh` | Recovery snapshot for an interrupted transition. |
| `deploy/history.jsonl` | Preparation, activation, and recovery history without credentials. |
| `logs/gateway.jsonl` | New bounded gateway diagnostics. |
| `tunnel/` | Existing profile, key, binary, health locators, and Tunnel logs. |

Do not move a release after building its virtual environment. Preparation
installs directly at the final commit path and marks the manifest prepared
only after its read-only probe succeeds. Partial builds can be retried when
their manifest and source hashes are intact. Releases are never automatically
deleted, so previous environments stay available for rollback. Source extraction
and its manifest are staged together before the final directory appears; an
interrupted extraction can be retried. A forced kill may leave an unused
`.prepare-*` staging directory, which is never activated.

## Prepare

Run from this checkout using its Python 3.11+ development environment:

```sh
git status --short
.venv/bin/python -m pytest -q
# Commit reviewed changes before preparing: dirty checkouts are rejected.
.venv/bin/python scripts/deploy.py prepare HEAD
```

Save the full `prepared` commit ID from the JSON output. Preparation:

1. Archives the committed tree, rejecting links, traversal, and runtime files.
2. Links the existing mode-0600 `connection.json` into the release.
3. Runs `uv sync --locked --offline --no-dev --no-editable` with the current
   interpreter, isolated from inherited uv/virtualenv overrides.
4. Runs a stdio MCP probe against the candidate gateway: initialization, exact
   tool catalog, Vault listing, and Recent Changes (`days=1`, `limit=3`).
5. Verifies source hashes/modes and records a prepared manifest.

No Tunnel restart occurs during preparation. Its offline build assumes the
locked packages and build dependencies are cached. A build or probe failure
leaves the old service running; diagnose it before proceeding. Raw subprocess
output is suppressed because it can contain configuration or Vault data.

## Activate

```sh
.venv/bin/python scripts/deploy.py activate FULL_PREPARED_COMMIT_ID
.venv/bin/python scripts/deploy.py status
```

Activation rechecks release integrity and reruns the candidate probe. It
records the old launcher/pointer/state, switches the launcher/current pointer,
then restarts only `gui/<uid>/com.foreignlab.obsidian-chatgpt-tunnel`.
ChatGPT is briefly unavailable during this restart. The health gate requires
a new Tunnel PID, healthy/ready endpoints, and a successful control-plane
poll within 60 seconds. A second probe uses the actual installed launcher.
All probes, including rollback and recovery checks, run in a prepared release's
environment, so they do not require MCP in the deployment command's interpreter.

If any step after switching fails, the command restores the snapshot,
restarts, and checks the old deployment. It reports an error even when
recovery succeeds, so a failed deployment is not mistaken for a successful
one. A failed recovery leaves `pending.json` and requires `recover`.

After activation, use the connected ChatGPT/Obsidian app to request Recent
Changes and confirm a matching successful tool event in the diagnostic log.
Local probes do not establish the full remote connector path. Record the
commit ID and which paths were actually verified.

## Roll back

```sh
.venv/bin/python scripts/deploy.py rollback
.venv/bin/python scripts/deploy.py status
```

The first rollback restores the exact legacy launcher. It checks the old
catalog and Vault listing but skips Recent Changes because the legacy version
has the known DQL/40012 bug. Later rollbacks select the previous prepared
commit and verify Recent Changes too. Each successful transition remembers
its prior target, so `rollback` can switch back again.

## Recover after an interruption

```sh
.venv/bin/python scripts/deploy.py status
.venv/bin/python scripts/deploy.py recover
```

If `pending` is true, preparation and activation stop instead of overwriting
the recovery snapshot. `recover` restores that snapshot and verifies the
prior service. If recovery cannot establish health, it keeps the snapshot
and returns an error. Check Obsidian, the Tunnel, and launchd before retrying;
do not delete the journal to bypass this check.

Only one deployment command can mutate runtime state at a time. Do not edit
`launch.sh`, `current`, or prepared source files by hand. `status` is read-only
and can be used while a transition is in progress.

## Diagnostics and retention

The managed launcher sets `OBSIDIAN_DIAGNOSTICS_DIR` to the runtime `logs/`
directory. Gateway records go to `gateway.jsonl`, rotated at 10 MiB with five
backups (`.1` through `.5`): about 60 MiB total. Retention is capacity-based,
not a fixed number of days. Files are private (0600); newly created log
directories are 0700. A shared file lock serializes rotation and writes from
multiple gateway processes. Old owned backups are replaced during rotation.

If this store is unavailable, diagnostics fall back to stderr without turning
a completed tool operation into a failure. Existing Tunnel and launchd logs
are not rotated, deleted, or retroactively redacted by this change. See
[diagnostics](diagnostics.md) for event meanings and privacy boundaries.

```sh
log="$HOME/.local/share/obsidian-chatgpt-mcp/logs/gateway.jsonl"
rg '"event":"(gateway_failed|tool_failed|api_error|library_error)"' "$log"
rg '"call_id":"REPLACE_WITH_CALL_ID"' "$log"
```

## Scope and remaining operational checks

The deployment does not change the Tunnel identity/key, plugin authentication,
CA certificate, Vault, or other clients' shared installation. macOS reboot and
sleep/wake behavior require separate testing. Run deployment commands as the
logged-in user who owns the LaunchAgent, without `sudo`.
