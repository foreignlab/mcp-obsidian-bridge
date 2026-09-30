# Deploying the shared stdio client

This runbook manages an existing shared installation at
`~/.local/share/mcp-obsidian-tls/` on macOS or Linux. Each managed source commit
has its own locked environment. A stable launcher selects the environment for
new client connections. [ChatGPT deployment](deployment.md) has a separate
runtime and procedure.

## Prerequisites and layout

Use Python 3.11+, Git, and uv. Run the commands below from a clean, reviewed
source checkout with its development environment available:

```sh
uv sync --locked --group dev
```

Preparation builds at `releases/<full-commit>/` using locked, offline,
non-editable installation. The required distributions and build dependencies
must already be cached. If preparation fails because they are missing, populate
the cache in the selected source checkout with `uv sync --locked --no-editable`
before retrying preparation. That cache step can access the network and changes
the checkout's development environment; it does not select a runtime release.

The root retains the original source and metadata for legacy rollback. Managed
environments go under `releases/`; `current` selects one, and `launch.sh` starts
it. Private state, the original startup record, a transition journal, and
content-free history are under `deploy-shared/`. Old releases stay available.
Build environments at their final paths; do not move them or edit them in place.

The source checkout, prepared environments, and connected client processes are
separate. Keep the checkout available for maintenance and recovery commands.
Use `--root`, `--repo`, `--uv`, or `--python` before the command for nondefault
installations. The manager's operator interpreter needs only the standard
library; probes run in a prepared release's environment.

## Prepare and adopt the launcher

Choose a reviewed commit from this fork. A source date tag may be supplied;
preparation resolves it to a full SHA. Use an existing private client entry:
Codex TOML `mcp_servers` and JSON `mcpServers` formats are supported. The entry
must supply the API key, host, port, HTTPS protocol, and readable CA bundle.
Connection values remain in that configuration and are never copied into
release manifests or output.

The examples use synthetic paths and a server entry named `obsidian`. Replace
them locally. If the entry has no `cwd`, supply `--legacy-cwd` with the working
directory to preserve for its original startup route.

```sh
.venv/bin/python scripts/deploy_shared.py \
  --client-config /absolute/path/to/private-client-config.toml \
  --server obsidian --legacy-cwd /absolute/path/to/original-working-directory \
  prepare FULL_REVIEWED_SOURCE_SHA
```

Save the full SHA in the `prepared` result. Preparation preserves the legacy
installation and current startup route; it does not create or switch the
launcher. It validates source files and the installed package inventory, then
checks the candidate MCP identity, catalog, Vault listing, and Recent Changes.
Probe output contains only counts. Missing live checks are not success.

After preparation, with authorization to activate the shared runtime:

```sh
.venv/bin/python scripts/deploy_shared.py \
  --client-config /absolute/path/to/private-client-config.toml \
  --server obsidian activate FULL_PREPARED_SOURCE_SHA
.venv/bin/python scripts/deploy_shared.py status
```

Require `selected` to match that SHA, `pending` to be false, and `integrity` to
be `verified`. The initial activation also checks the retained legacy startup
route and verifies the new launcher before reporting success.

For each client intended to use this shared runtime, replace its startup
command and old uvx arguments with the absolute launcher path, retaining the
existing connection environment. For example:

```toml
[mcp_servers.obsidian]
command = "/absolute/path/to/mcp-obsidian-tls/launch.sh"
args = []
# Keep the existing [mcp_servers.obsidian.env] table and its private values.
```

Clients using JSON keep their `mcpServers` entry and `env` object, with the same
command and empty arguments. The manager does not edit these files or stop
other client sessions. Reconnect each intended client and verify a read request
through that client. A launcher probe alone does not establish that the client
configuration was changed or that an existing process reconnected.

## Later updates and verification

Use the same preparation and activation commands for another reviewed commit;
`--legacy-cwd` is only needed to capture the initial legacy record. Keep using
the private configuration entry even after it points at `launch.sh`: probes read
its connection environment, while the original legacy command remains frozen.
A failed or incomplete preparation cannot be activated. Retry intact partial
preparations after addressing the build/probe failure.

The selected revision is used for new connections; already running processes
keep their original code until reconnection. No background service is restarted.
For a standalone read-only check:

```sh
.venv/bin/python scripts/deploy_shared.py \
  --client-config /absolute/path/to/private-client-config.toml \
  --server obsidian probe
```

Record the full source SHA, any source tag, date, previous target, and checks
actually performed for each client. Keep private paths/configuration locally;
public records in `docs/deployments/` contain sanitized results and limitations.
The shared server retains its own unrestricted REST entry point; it does not
gain the ChatGPT gateway's write-folder policy.

## Rollback and recovery

```sh
.venv/bin/python scripts/deploy_shared.py \
  --client-config /absolute/path/to/private-client-config.toml \
  --server obsidian rollback
.venv/bin/python scripts/deploy_shared.py status
```

Rollback selects and verifies the recorded previous target. After initial
adoption it can use the original uvx command through the stable launcher. That
legacy route inherits its original cache and dependency behavior; it is not a
locked managed snapshot. The original source/metadata must remain intact.
After two managed deployments, rollback selects a retained locked environment.
Reconnect clients after rollback. A subsequent rollback can select the target
that was active before the last rollback.

A failed switch restores and verifies the prior route, but still exits with a
failure result. If recovery verification fails or a process is interrupted,
`pending` remains true and further mutations stop. Keep the journal and run:

```sh
.venv/bin/python scripts/deploy_shared.py \
  --client-config /absolute/path/to/private-client-config.toml \
  --server obsidian recover
.venv/bin/python scripts/deploy_shared.py status
```

Recovery restores the saved pointer, launcher, and state, then checks the old
startup route through a retained prepared environment. It clears the journal
only after verification. Diagnose an unavailable Obsidian connection, changed
legacy source, or damaged prepared environment before retrying. Do not hand-edit
the pointer, release files, or journal to bypass checks. `status` is read-only and
requires no credentials; its selection report does not inventory running clients.
