# Managed shared-client deployment

## Intent and approved approach

The maintainer wants reusable preparation, verification, activation, rollback,
and recovery for the shared stdio installation. A successful deployment must
identify the exact committed source, use its locked dependencies, and provide
a verified way back when switching fails. ChatGPT deployment remains a separate
installation and procedure.

The agreed approach stores one environment per source commit and starts the
selected environment through a stable launcher. Each client adopts that launcher
once, retaining its connection environment. Later changes select an environment;
existing client processes continue until reconnection. This document specifies
the proposed implementation, not an already deployed feature.

## Current installation and migration boundary

The existing shared runtime uses `uvx --from <shared-runtime> mcp-obsidian`, with
MCP and Requests overrides in the client configuration. Its project metadata
has broader dependencies than the repository's lockfile. A previous source
update needed an explicit uvx cache rebuild before the new code was used.

Keep that runtime's source, metadata, certificates, and private configuration in
place during migration. Preserve the original launch command, arguments, and
working directory in a private legacy record. Capture the source and package
metadata hashes needed to detect changes before returning to that installation.
Do not copy connection environment values into release source or manifests.

The first rollback restores the original uvx startup route through the stable
launcher. It inherits that route's existing cache and dependency behavior; it
is not a newly locked snapshot. Probe it before initial activation and again
when rolling back. A failed legacy probe must report failure and retain recovery
state. Subsequent managed-to-managed rollbacks select a retained locked
environment. Do not rebuild or modify the legacy uvx installation as part of
preparing managed releases.

## Scope and components

- `scripts/deploy_shared.py`: standard-library CLI and shared deployment state
  machine, including source extraction, building, integrity checks, and journaled
  transitions. Keep it separate from `scripts/deploy.py`'s ChatGPT service logic.
- `scripts/probe_shared.py`: read-only stdio MCP probe using the installed MCP
  1.29.0 SDK. It checks startup, the shared catalog, Vault listing, and Recent
  Changes without emitting note names or contents.
- `docs/shared-deployment.md`: operator runbook, initial client adoption,
  subsequent updates, verification, rollback, and interruption recovery.
- Focused tests under `tests/`: temporary Git repositories, fake runtimes,
  injected subprocess/build failures, and synthetic MCP/REST fixtures.

Support macOS and Linux with Python 3.11+, Git, and uv. Add no package dependency.
Use the source commit's `uv.lock`; retain the existing MCP and Requests pins.
The inspected local tools are uv 0.12.16 and MCP 1.29.0. Implementation must use
the installed SDK's v1 interfaces, even when current documentation includes v2
examples. Installation or deployment to the real runtime is a later action.

## Runtime layout

The default root remains `~/.local/share/mcp-obsidian-tls/`. It contains:

| Path | Role |
| --- | --- |
| Existing source and metadata | Preserved legacy installation. |
| `releases/<full-commit>/` | Archived committed source, fixed-path `.venv`, and manifest. |
| `current` | Atomic symlink selecting a managed release; absent in legacy state. |
| `launch.sh` | Stable launcher: resolve `current` once or use the recorded legacy route. |
| `deploy-shared/state.json` | Selected revision and previous target, initially `legacy`. |
| `deploy-shared/legacy.json` | Original startup arguments and integrity inventory. |
| `deploy-shared/pending.json` | Snapshot required to restore an interrupted transition. |
| `deploy-shared/history.jsonl` | Bounded-field preparation/transition outcomes without credentials. |
| `deploy-shared/lock` | Exclusive mutation lock. |

New control directories are mode 0700; private metadata and history are 0600;
the launcher is executable only by its owner. Release directories and previous
environments are retained. Virtual environments are built at their final path
and never moved after installation.

The launcher uses an absolute release path, changes to that release directory,
and execs its installed `mcp-obsidian` entry point. It emits no normal stdout of
its own. Client connection variables are inherited. Prevent inherited Python
module-path and uv/virtualenv overrides from selecting checkout code or another
environment. Legacy startup retains the original command and working directory.

## Configuration and CLI

Use an explicit `--client-config PATH --server NAME` for operations needing a
connection. Accept Codex TOML `mcp_servers` and JSON `mcpServers` formats; read
the named entry only. Validate the command/arguments/environment types. The
first preparation captures the legacy command; later client entries may point
to the managed launcher. Connection values remain in the client's private
configuration and are read afresh for probes.

Require HTTPS and a readable configured CA bundle for the probe path. Never
disable certificate verification. A probe must receive the selected entry's
connection variables, not an unrelated ambient API key or dotenv file. Reject
missing required connection settings with a fixed error that omits their values.

Expose `--root`, `--repo`, `--uv`, and `--python` overrides for testing and
nondefault installations. Discover uv on PATH by default. Commands are:

| Command | Behavior |
| --- | --- |
| `prepare REVISION` | Resolve a tag/commit to its full SHA; build and probe without switching. |
| `activate FULL_SHA` | Switch to that prepared, verified environment and verify the launcher. |
| `rollback` | Transition to the recorded previous target, including the legacy route. |
| `recover` | Restore a pending snapshot and verify its startup route. |
| `status` | Read selected/previous targets and pending/integrity state; no connection required. |
| `probe` | Exercise the selected startup route without changing deployment state. |

Output is a small JSON result. Status describes the revision selected for new
connections; it cannot establish the version used by every running client.
The runbook instructs operators to reconnect each client after adoption or a
transition. The manager never edits client configuration or stops other sessions.

## Preparation and integrity

Require a clean source worktree and a resolvable committed revision. Archive
that Git tree, rejecting traversal, links, unsupported archive members, and
private runtime/configuration files. Stage source extraction and its manifest
together, then build at the final release path using
`uv sync --locked --offline --no-dev --no-editable` with the selected Python.
Suppress raw build output. Missing cached dependencies cause a preparation
failure with no active-route change; cache population is an explicit operator
step in the runbook.

Record the full SHA, source file inventory with hashes/modes, preparation status,
and sanitized probe counts. Verify that the installed `mcp_obsidian` Python
files match the committed package source, including the file inventory. This
checks the code actually installed in the environment, rather than only the
archived source beside it. Reject a modified, incomplete, or mismatched release
before activation. An intact partial preparation can be retried; it cannot be
activated until the build and probe succeed.

## Transitions and recovery

Serialize preparation, activation, rollback, and recovery with a nonblocking
exclusive file lock. A pending transition blocks other mutations until recovery.
Before switching, verify the current state and target integrity and probe the
candidate. For initial adoption also verify and probe the retained legacy route.

Write the prior pointer, launcher existence/content/mode, and state to the
private pending snapshot before mutation. Install the stable launcher on first
activation and atomically change the pointer. Probe through the installed
launcher, then commit state/history and clear the pending snapshot.

Any failure after mutation restores the prior snapshot and probes the restored
route. Report activation failure even when restoration succeeds. If restoration
or verification fails, retain the snapshot and return an explicit recovery
required result. `recover` can retry without overwriting the journal. Include
process interruption tests for boundaries between pointer/state/journal writes.
For a failed first activation, restore the original absence of a launcher as well
as legacy state. A successful rollback after adoption keeps the stable launcher
usable through its legacy fallback.

## Probe and privacy contract

Use `ClientSession`, `StdioServerParameters`, and `stdio_client` with bounded
timeouts and captured child stderr. Run verification in a prepared environment,
so activation/recovery need only standard-library Python in the operator shell.
Verify `mcp-obsidian` server identity and the exact shared catalog, rejecting
duplicates. Compare against the catalog supported by this implementation; the
legacy periodic-note tools may be advertised but are not exercised.

Call Vault listing and Recent Changes (`days=1`, `limit=3`). Validate result
types, the Recent Changes boundary/order, and MCP error flags. Report only tool
and row counts and coarse error categories. No probe writes to the Vault.
Capture dependency logs and exception groups without relaying raw subprocess
text, configuration values, note paths, or tool responses. No credentials or
private Vault data belong in Git, test fixtures, or public deployment records.

## Initial adoption and acceptance

After the implementation is reviewed and merged, a separately authorized
installation prepares the chosen source SHA using an existing private client
entry. Preparation preserves its old startup route. Activation creates the
managed launcher and validates it before clients are changed. The operator
then changes each intended client's command to the absolute `launch.sh`, removes
the old uvx arguments, retains its connection environment, and reconnects it.
Record which actual client paths were tested; a local probe does not establish
that every client's configuration or reconnection succeeded.

Acceptance requires focused regression tests and the full synthetic suite:
failed builds/probes leave selection unchanged; source and installed-package
tampering block activation; managed and initial legacy rollbacks work; interrupted
and failed recovery retains a usable journal; concurrency is rejected; actual
launcher subprocesses select one fixed environment; inherited overrides cannot
redirect it; and errors expose no sentinel secrets or synthetic note contents.
Run the repository's local review and PR/CI gates before merge. Real-runtime
adoption, source-release publication, and feature changes to PATCH or daily notes
are outside this implementation's automatic effects.
