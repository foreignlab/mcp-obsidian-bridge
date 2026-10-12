# Installing the managed shared client

Use this guide on a new macOS or Linux PC to reproduce the managed shared
stdio layout. Codex, Claude Code, and Claude Desktop use the same launcher;
each PC builds its own environments from the selected source SHA. Windows is
outside the tested scope. This procedure does not install the ChatGPT gateway.

If `~/.local/share/mcp-obsidian-bridge/` already exists, use the
[shared deployment runbook](shared-deployment.md) to inspect and update it.
Do not run the bootstrap commands over an existing runtime. An installation at
the former default `~/.local/share/mcp-obsidian-tls/` is a separate runtime:
this guide does not move or replace it, and its clients keep using it until
their settings are changed.

## Layout and prerequisites

| Location | Purpose |
| --- | --- |
| A separate source checkout | Reviewed Git objects and maintenance commands |
| `~/.local/share/mcp-obsidian-bridge/` | Retained initial source and Python environment for first rollback |
| `~/.local/share/mcp-obsidian-bridge/releases/<SHA>/` | Managed source snapshot and locked, non-editable `.venv` |
| `~/.local/share/mcp-obsidian-bridge/launch.sh` | Fixed client startup command |
| `~/.local/share/mcp-obsidian-bridge/deploy-shared/` | Private selection, original startup record, and recovery state |
| `~/.config/mcp-obsidian-bridge/` | Private operator profile and trusted public certificate |

The source checkout may live in your usual code directory. It is separate from
the runtime; changing its branch does not change the selected release. The
initial environment is retained for first rollback. Subsequent updates use
`prepare` and `activate`, not Git checkout or dependency sync in runtime paths.

Install Git, uv, and Python 3.13 (or let uv obtain it with `--python 3.13`).
The project supports Python 3.11+; the commands below choose a version covered
by this fork's CI. uv installation and dependency setup can access the network.
Keep Obsidian running with the target Vault open and Local REST API enabled.

```sh
mkdir -p "$HOME/.config/mcp-obsidian-bridge"
chmod 700 "$HOME/.config/mcp-obsidian-bridge"
```

Use the [README certificate setup](../README.md#tls-certificate-setup) to save
the API's public CA certificate, or its self-signed server certificate, as
`~/.config/mcp-obsidian-bridge/obsidian-ca.crt`. PCs connecting to the same API can
trust the same public certificate. Each connection hostname or IP must match
the server certificate; never copy its private key.

## 1. Select the source

Run the steps in the same shell, one command at a time, and stop on any failure.
The example selects the reviewed initial
baseline below; replace it with another reviewed full SHA or published source
tag when updating the installation target.

```sh
BRIDGE_SOURCE="$HOME/Documents/ghq/github.com/foreignlab/mcp-obsidian-bridge"
BRIDGE_ROOT="$HOME/.local/share/mcp-obsidian-bridge"
BRIDGE_PROFILE="$HOME/.config/mcp-obsidian-bridge/shared-client.json"
BRIDGE_REF=4766d8275e9a729a7a00ae87415c500a3c66795a

git clone https://github.com/foreignlab/mcp-obsidian-bridge.git "$BRIDGE_SOURCE"
git -C "$BRIDGE_SOURCE" checkout --detach "$BRIDGE_REF"
BRIDGE_REVISION=$(git -C "$BRIDGE_SOURCE" rev-parse HEAD)
uv --directory "$BRIDGE_SOURCE" sync --locked --no-dev --no-editable --python 3.13
```

If you already have a clean maintenance checkout, set `BRIDGE_SOURCE` to that
path and select the reviewed revision there instead of cloning again. Do not
switch a checkout being used by another development session. The source `.venv`
is the operator environment, not the client's execution environment.

## 2. Create the retained initial environment

The deployment manager requires an original startup route for first rollback.
Create that route at the runtime root, then let the manager build and select a
separate release. The local clone below copies Git objects from the source
checkout; it does not copy another PC's `.venv`, configuration, or deployment state.

```sh
(
  set -eu
  if [ -e "$BRIDGE_ROOT" ] || [ -L "$BRIDGE_ROOT" ]; then
    printf '%s\n' 'Shared runtime already exists; use the deployment runbook.' >&2
    exit 1
  fi
  git clone --no-hardlinks --no-checkout "$BRIDGE_SOURCE" "$BRIDGE_ROOT"
  git -C "$BRIDGE_ROOT" checkout --detach "$BRIDGE_REVISION"
  uv --directory "$BRIDGE_ROOT" sync --locked --no-dev --no-editable --python 3.13
)
```

Stop if a command fails. Leave an incomplete root in place for diagnosis rather
than applying this block again. After installation, keep this initial checkout
and `.venv` unchanged: the manager records the original source and startup route
for rollback.
The online sync also caches the packages and build dependencies needed by the
manager's later offline builds. Cache misses during preparation are failures;
follow the cache guidance in the [deployment runbook](shared-deployment.md#prerequisites-and-layout).

## 3. Create the private operator profile

Save the JSON below as `BRIDGE_PROFILE` with a local editor, in the private
directory created above. Replace every placeholder with that PC's actual values;
do not put the API key in a shell command or commit this file.

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "/absolute/home/.local/share/mcp-obsidian-bridge/.venv/bin/mcp-obsidian",
      "args": [],
      "cwd": "/absolute/home/.local/share/mcp-obsidian-bridge",
      "env": {
        "OBSIDIAN_API_KEY": "REPLACE_LOCALLY",
        "OBSIDIAN_HOST": "127.0.0.1",
        "OBSIDIAN_PORT": "27124",
        "OBSIDIAN_PROTOCOL": "https",
        "REQUESTS_CA_BUNDLE": "/absolute/home/.config/mcp-obsidian-bridge/obsidian-ca.crt"
      }
    }
  }
}
```

```sh
chmod 600 "$BRIDGE_PROFILE"
```

`/absolute/home` means the real home directory, such as `/Users/YOUR_USER` on
macOS or `/home/YOUR_USER` on Linux. JSON and TOML values do not expand `~` or
`$HOME`. `127.0.0.1` means Obsidian on that PC. For an API on another machine,
use its reachable certificate-covered hostname and ensure the HTTPS connection
is available; obtain the API key and public certificate from that target API.

This profile is for deployment commands. It initially describes the retained
startup route and supplies connection values. It can stay at that route after
activation: managed probes use the selected release, while later preparation
reuses the separately recorded original route. Actual client settings will
point to `launch.sh` in step 5.

## 4. Prepare and activate the managed release

```sh
"$BRIDGE_SOURCE/.venv/bin/python" "$BRIDGE_SOURCE/scripts/deploy_shared.py" \
  --root "$BRIDGE_ROOT" --repo "$BRIDGE_SOURCE" \
  --client-config "$BRIDGE_PROFILE" --server obsidian \
  prepare "$BRIDGE_REVISION"

"$BRIDGE_SOURCE/.venv/bin/python" "$BRIDGE_SOURCE/scripts/deploy_shared.py" \
  --root "$BRIDGE_ROOT" --repo "$BRIDGE_SOURCE" \
  --client-config "$BRIDGE_PROFILE" --server obsidian \
  activate "$BRIDGE_REVISION"

"$BRIDGE_SOURCE/.venv/bin/python" "$BRIDGE_SOURCE/scripts/deploy_shared.py" \
  --root "$BRIDGE_ROOT" --repo "$BRIDGE_SOURCE" status
```

Run activation only after preparation succeeds and its returned full SHA equals
`BRIDGE_REVISION`. Require `selected` to equal that SHA, `previous` to be
`legacy`, `pending` to be false, and `integrity` to be `verified`. Preparation
checks the 15-tool catalog, Vault listing, and Recent Changes; activation checks
the retained initial route and the installed launcher. Output contains counts,
not note contents. Failed live checks do not establish a usable installation.

## 5. Configure clients

Close the clients whose settings you will edit and back up their existing
configuration files privately. In particular, a running Claude Code session can
rewrite `~/.claude.json`. Keep unrelated servers and client-specific tool or
approval settings. Preserve an existing server name; examples use `obsidian`.

For the user configuration paths below (the Desktop path is for macOS):

```sh
BRIDGE_BACKUP_STAMP=$(date +%Y%m%d%H%M%S)
for BRIDGE_CLIENT_CONFIG in \
  "$HOME/.codex/config.toml" \
  "$HOME/.claude.json" \
  "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
do
  if [ -f "$BRIDGE_CLIENT_CONFIG" ]; then
    cp -p "$BRIDGE_CLIENT_CONFIG" "$BRIDGE_CLIENT_CONFIG.bak-$BRIDGE_BACKUP_STAMP"
    chmod 600 "$BRIDGE_CLIENT_CONFIG.bak-$BRIDGE_BACKUP_STAMP"
  fi
done
```

| Client | Configuration |
| --- | --- |
| Codex | `~/.codex/config.toml`, `mcp_servers.obsidian` |
| Claude Code | `~/.claude.json`, `mcpServers.obsidian` for a user-scoped entry; check for project overrides |
| Claude Desktop on macOS | `~/Library/Application Support/Claude/claude_desktop_config.json`, `mcpServers.obsidian` |

Use an absolute launcher path and empty arguments for all clients. For Codex:

```toml
[mcp_servers.obsidian]
command = "/absolute/home/.local/share/mcp-obsidian-bridge/launch.sh"
args = []

[mcp_servers.obsidian.env]
OBSIDIAN_API_KEY = "REPLACE_LOCALLY"
OBSIDIAN_HOST = "127.0.0.1"
OBSIDIAN_PORT = "27124"
OBSIDIAN_PROTOCOL = "https"
REQUESTS_CA_BUNDLE = "/absolute/home/.config/mcp-obsidian-bridge/obsidian-ca.crt"
```

For either JSON client, use the [README client example](../README.md#local-client-configuration).
Reuse the operator profile's connection values. The launcher directly executes
the selected release's entry point; client settings do not need to locate uv or
Python through `PATH`. Preparation still needs uv available to the operator;
use `--uv /absolute/path/to/uv` before the manager command if necessary.

## 6. Verify each client and record the installation

Reconnect each client and check its loaded startup command and empty arguments.
Through that client, list the Vault root and request Recent Changes with
`days=1, limit=3`. Report success and counts only. Check that its actual MCP
process executes from `BRIDGE_ROOT/releases/BRIDGE_REVISION/.venv/bin/`; an
already running process can retain an earlier startup route until reconnection.
Inspect only the executable/script path, not all arguments or environment values.

A deployment probe, another client's successful read, or a tool name alone
does not prove that this client uses the selected revision. For example,
`obsidian_get_recent_changes` also exists upstream. Record the source SHA,
date, client, actual launch path, and checks performed privately on each PC.
Treat untested client connections and write compatibility as unverified.

## Updates and rollback

Use the [shared deployment runbook](shared-deployment.md) for later preparation,
activation, rollback, and interrupted-transition recovery. Select a reviewed
revision in the source checkout and prepare it before activation. All clients
keep the same `launch.sh` command and must reconnect after selection changes.
Each PC has its own selected state; updating one PC does not update another.

First rollback selects the retained initial environment created in step 2.
After a later managed update, rollback selects the previous managed release.
Restore client configuration backups if reverting the client's setup itself;
that is separate from selecting an earlier runtime with the manager. Keep the
initial environment, retained releases, certificate, and private state available.
Do not copy prepared environments or deployment state between PCs.
