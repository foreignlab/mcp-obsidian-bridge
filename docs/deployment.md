# Deploying the ChatGPT gateway

Deployment is local and commit-addressed. The ChatGPT runtime has its own
virtual environment; the shared stdio installation used by other clients is
not modified. Keep the working checkout available for deployment and recovery
commands.

For the shared stdio installation, use the separate
[shared deployment runbook](shared-deployment.md).

## Runtime locations

One source repository produces two runtimes, each with its own manager:

| Location | Contents | Managed by |
| --- | --- | --- |
| Source checkout, for example `~/Documents/ghq/github.com/foreignlab/mcp-obsidian-bridge/` | Git history and maintenance commands | Git |
| `~/.local/share/mcp-obsidian-bridge/` | Shared stdio server for Codex, Claude Code, and Claude Desktop, on each PC | `scripts/deploy_shared.py` |
| `~/.local/share/mcp-obsidian-chatgpt/` | Restricted ChatGPT gateway behind the Secure MCP Tunnel, on the gateway host only | `scripts/deploy.py` |

The runtimes are separate so that each one selects its own source commit, updates
and rolls back without touching the other, and keeps its own configuration.
Updating the shared server only needs clients to reconnect. Updating the gateway
restarts the Tunnel and briefly interrupts ChatGPT. The gateway also enforces the
write allowlist, which the shared server does not have. The shared runtime root
is itself a Git checkout retained for rollback, so the gateway should not live inside it.

Paths and the LaunchAgent label in this runbook, the [gateway guide](../chatgpt/README.md),
and [diagnostics](diagnostics.md) are the defaults:

| Item | Default | Former name |
| --- | --- | --- |
| Gateway runtime root | `~/.local/share/mcp-obsidian-chatgpt/` | `~/.local/share/obsidian-chatgpt-mcp/` |
| LaunchAgent label | `com.foreignlab.mcp-obsidian-chatgpt-tunnel` | `com.foreignlab.obsidian-chatgpt-tunnel` |

An installation that has not been moved keeps the former names, including its
plist `~/Library/LaunchAgents/<label>.plist`. For it, substitute the former names
in these documents and pass both `--root` and `--label` to every manager command,
or [move it to the defaults](#moving-an-installation-to-the-default-names).
Activation, rollback, and recovery stop before changing files when the label's
LaunchAgent is not loaded; start a stopped LaunchAgent before running them.

The [source release policy](releases.md) defines date tags and GitHub Releases.
This runbook identifies runtime releases by full source commit SHA; record that
SHA and the source tag, when available, for each deployment.

## Layout

Under `~/.local/share/mcp-obsidian-chatgpt/`:

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

The managed `launch.sh` still names `obsidian-chatgpt-mcp` in its comment. The
manager compares an installation's launcher byte-for-byte with its template, so
the template stays unchanged and an installation at the former root remains
manageable. Renaming the directory does not move an installation: `launch.sh`,
`current`, each release's `connection.json` link, the LaunchAgent plist, and the
Tunnel profile name the root, so a new root needs its own prepared releases.

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
then restarts only `gui/<uid>/com.foreignlab.mcp-obsidian-chatgpt-tunnel`, or the
label passed with `--label`.
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

## Moving an installation to the default names

This moves an installation from the former root and label to the defaults while
keeping its active release. Change the release separately, before or after the
move. The original installation was moved this way on 2026-10-12.

ChatGPT cannot use the gateway from the LaunchAgent switch (step 6) until the
gateway that the new Tunnel starts finishes its startup. If the Vault is in a
privacy-protected folder such as `~/Documents`, that startup waits for permission
granted at the Mac's screen (step 7). Plan the move for a time when someone can operate that
screen. Run the commands as the logged-in user, from this checkout:

```sh
OLD="$HOME/.local/share/obsidian-chatgpt-mcp"
NEW="$HOME/.local/share/mcp-obsidian-chatgpt"
OLD_LABEL=com.foreignlab.obsidian-chatgpt-tunnel
NEW_LABEL=com.foreignlab.mcp-obsidian-chatgpt-tunnel
LA="$HOME/Library/LaunchAgents"
U=$(id -u)
```

1. Check the old installation and note its `active` commit. `pending` must be
   false. If `active` is `legacy`, prepare and activate a release in the old root
   first; these steps move a prepared release.

   ```sh
   .venv/bin/python scripts/deploy.py --root "$OLD" --label "$OLD_LABEL" status
   ```

2. Create the new root and copy the private configuration and Tunnel files.
   The subshell stops at the first failure, including `mkdir` when the new root
   already exists; do not continue if it reports an error.

   ```sh
   (
     set -e
     umask 077
     mkdir "$NEW" "$NEW/tunnel"
     cp -p "$OLD/connection.json" "$NEW/"
     cp -pR "$OLD/tunnel/v0.0.14" "$OLD/tunnel/profiles" "$NEW/tunnel/"
     cp -p "$OLD/tunnel/runtime-api-key" "$OLD/tunnel/release.json" "$NEW/tunnel/"
   )
   ```

3. Point the Tunnel profile's runtime key file and MCP command at the new root,
   then confirm that no reference to the old root remains (count `0`). Do not
   print the profile into shared output.

   ```sh
   sed -i '' "s#$OLD/#$NEW/#g" "$NEW/tunnel/profiles/obsidian-chatgpt.yaml"
   grep -c -F "$OLD" "$NEW/tunnel/profiles/obsidian-chatgpt.yaml"
   ```

   The profile's health and log files under `~/Library/Application Support/tunnel-client/`
   stay where they are; only one of the two Tunnels runs at a time.

4. Create a temporary launcher that runs the old installation. Activation keeps
   it as `deploy/legacy-launch.sh`, so the new root's first rollback returns to
   the old installation. Then prepare the active commit in the new root; the
   commit must exist in this checkout, so fetch first if it is behind.

   ```sh
   printf '#!/bin/sh\nset -eu\nexec %s/launch.sh\n' "$OLD" > "$NEW/launch.sh"
   chmod 700 "$NEW/launch.sh"
   .venv/bin/python scripts/deploy.py prepare FULL_ACTIVE_COMMIT_ID
   ```

5. Create the new plist from the old one, then confirm that it is valid and that
   no old name remains (count `0`).

   ```sh
   sed -e "s#$OLD#$NEW#g" -e "s#<string>$OLD_LABEL</string>#<string>$NEW_LABEL</string>#" \
     "$LA/$OLD_LABEL.plist" > "$LA/$NEW_LABEL.plist"
   chmod 600 "$LA/$NEW_LABEL.plist"
   plutil -lint "$LA/$NEW_LABEL.plist"
   grep -c -F -e "$OLD" -e "$OLD_LABEL" "$LA/$NEW_LABEL.plist"
   ```

6. Switch the LaunchAgent. ChatGPT is unavailable from here until the gateway
   finishes its startup (step 7).
   Repeat the health check until it reports `"result":"ok"`; this took about one
   minute on the original installation.

   ```sh
   launchctl bootout "gui/$U/$OLD_LABEL"
   launchctl disable "gui/$U/$OLD_LABEL"
   launchctl bootstrap "gui/$U" "$LA/$NEW_LABEL.plist"
   "$NEW/tunnel/v0.0.14/tunnel-client" health --url-file "$NEW/tunnel/launchd-health.url" \
     --pid-file "$NEW/tunnel/launchd.pid" --require-control-plane-poll --json
   ```

7. Grant the copied `tunnel-client` access to the Vault's folder at the Mac's
   screen, as described in [macOS automatic startup](../chatgpt/README.md#macos-automatic-startup).
   Until then, the gateway that the Tunnel starts records `gateway_starting` and
   nothing more, and ChatGPT requests time out. Before step 8 it runs through the
   temporary launcher and records to `$OLD/logs/gateway.jsonl`; afterwards it
   records to `$NEW/logs/gateway.jsonl`. Tunnel health still reports ok.
   Activation probes run from the deployment command's own process, such as an
   SSH session, so they also pass. Access can be granted before or after step 8;
   on 2026-10-12 it was granted after step 8.

8. Activate the same commit. `status` should show it as `active` with `previous`
   set to `legacy`.

   ```sh
   .venv/bin/python scripts/deploy.py activate FULL_ACTIVE_COMMIT_ID
   .venv/bin/python scripts/deploy.py status
   ```

9. From ChatGPT, list the Vault and request Recent Changes. In
   `$NEW/logs/gateway.jsonl`, confirm `gateway_ready` and a `tool_completed` record
   for each call, timed after the request. Activation's own probes also write
   records to this log.

To revert the move, stop and disable the new label, then start the old one.
Disabling the new label keeps it from also starting at the next login. The move
does not change the old root's configuration, launcher, or releases, or the old
plist, and the old `tunnel-client` keeps its permission.

```sh
launchctl bootout "gui/$U/$NEW_LABEL"
launchctl disable "gui/$U/$NEW_LABEL"
launchctl enable "gui/$U/$OLD_LABEL"
launchctl bootstrap "gui/$U" "$LA/$OLD_LABEL.plist"
```

To try the move again after a revert, first move the new root and the new plist
aside, and run `launchctl enable "gui/$U/$NEW_LABEL"` before step 6.

Keep the old root and the disabled old plist until a later release is active in
the new root. Until then, the new root's first rollback runs the old root's launcher.

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
log="$HOME/.local/share/mcp-obsidian-chatgpt/logs/gateway.jsonl"
rg '"event":"(gateway_failed|tool_failed|api_error|library_error)"' "$log"
rg '"call_id":"REPLACE_WITH_CALL_ID"' "$log"
```

## Scope and remaining operational checks

The deployment does not change the Tunnel identity/key, plugin authentication,
CA certificate, Vault, or other clients' shared installation. macOS reboot and
sleep/wake behavior require separate testing. Run deployment commands as the
logged-in user who owns the LaunchAgent, without `sudo`.
