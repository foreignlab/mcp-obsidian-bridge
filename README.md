# mcp-obsidian-bridge

**A security-focused fork of [mcp-obsidian](https://github.com/MarkusPfundstein/mcp-obsidian) for ChatGPT and other remote MCP clients, with safer remote access in mind.**

Connect AI clients to Obsidian through the [Local REST API community plugin](https://github.com/coddingtonbear/obsidian-local-rest-api). This fork builds on the original MCP server with TLS certificate verification, a restricted gateway for ChatGPT, and compatibility fixes for the REST API. You can also use the shared server directly from a local MCP client.

This is a personal-use fork. Bug reports and suggestions are welcome through [Issues](https://github.com/foreignlab/mcp-obsidian-bridge/issues), but external pull requests are not accepted. General-purpose support and response times are not guaranteed.

## Components

### Tools

The server retains the upstream tools below. Names shown here are the names exposed to MCP clients; the upstream README abbreviates them.

- `obsidian_list_files_in_vault`: List files and directories at the root of the Vault.
- `obsidian_list_files_in_dir`: List files and directories in a specified directory.
- `obsidian_get_file_contents`: Read a single file.
- `obsidian_simple_search`: Search for text across the Vault.
- `obsidian_patch_content`: Insert or replace content relative to a heading, block reference, or frontmatter field.
- `obsidian_append_content`: Append content to a new or existing file.
- `obsidian_delete_file`: Delete a file or directory through the shared server. The ChatGPT gateway permits only one existing regular file per call and requires boolean `confirm=true`.

The fork also exposes these tools through both entry points:

- `obsidian_put_content`: Create a file or replace its entire contents.
- `obsidian_batch_get_file_contents`: Read multiple files.
- `obsidian_search_by_tag`: Find notes by tag.
- `obsidian_get_frontmatter`: Read a note's frontmatter.
- `obsidian_get_recent_changes`: List recently modified Markdown notes, newest first.

The shared server additionally registers `obsidian_complex_search`, `obsidian_get_periodic_note`, and `obsidian_get_recent_periodic_notes`. These are not exposed through the ChatGPT gateway. The periodic-note tools depend on legacy `/periodic/` endpoints and are unavailable with Local REST API versions that removed those endpoints; read daily notes by file path instead.

All ChatGPT writes are limited to configured folders. The shared server has no equivalent folder policy. See [Security model](#security-model) before choosing an entry point.

### Example prompts

Ask your AI client to use its Obsidian tools explicitly. For example:

- "Use Obsidian to read the notes from our last architecture discussion and summarize the decisions."
- "Search Obsidian for Azure Cosmos DB and explain the contexts in which it appears."
- "Summarize the last meeting notes and save the summary as `000_Inbox/meeting-summary.md`."

For the write example, create `000_Inbox` in the Vault and include it in the gateway's `write_folders` setting first.

## Requirements

### Shared requirements

This guide covers macOS and Linux. Windows is currently outside this fork's tested and documented scope.

For both ChatGPT and local clients:

- **Obsidian running with the target Vault open**, and the **Local REST API** community plugin installed and enabled.
- The plugin's **API key**, its **HTTPS endpoint**, and a **trusted certificate or CA bundle** for that endpoint. Follow [TLS certificate setup](#tls-certificate-setup) to obtain and configure it. The hostname or IP used to connect must match the certificate.
- **Python 3.11 or newer**, **[uv](https://docs.astral.sh/uv/)**, and **Git** for the source installation below. `uv sync --locked` installs the dependencies recorded in this fork's lockfile.
- Network access from the MCP process to Obsidian's HTTPS endpoint. On the same machine, the default is `https://127.0.0.1:27124`.

### For ChatGPT

These requirements describe this fork's documented **Secure MCP Tunnel** setup. Cloudflare is not required. ChatGPT also supports [remote HTTP MCP connections](https://developers.openai.com/api/docs/guides/developer-mode), but this repository's entry points use stdio and do not implement that transport.

For the documented setup, in addition to the shared requirements:

- ChatGPT developer-mode access and OpenAI Platform tunnel permissions: **Read + Manage** to create a tunnel, **Read + Use** to run or select it.
- An **OpenAI Secure MCP Tunnel**, associated with the target ChatGPT workspace, its **runtime API key**, and the **`tunnel-client`** executable. The tunnel key is separate from the Obsidian API key.
- Outbound HTTPS connectivity to OpenAI from the tunnel host; no public inbound listener is required for this connection pattern.
- Local filesystem access to the Vault from the gateway process, including `.obsidian/plugins/obsidian-local-rest-api/data.json`. The gateway checks paths and verifies that the API key belongs to that Vault.

See the [official Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) for account permissions, downloads, and network requirements. The optional managed deployment scripts in this repository use macOS `launchd`; they assume an existing runtime setup.

### For local MCP clients

Use a client that can launch an MCP server over **stdio**, such as Claude Desktop, Claude Code, or Codex. Configure its server command, arguments, and environment as described below. Local stdio use requires no ChatGPT account or tunnel.

## Configuration

### Shared Obsidian connection settings

| Variable | Purpose | Default |
| --- | --- | --- |
| `OBSIDIAN_API_KEY` | API key from the Local REST API plugin | Required |
| `OBSIDIAN_HOST` | Hostname or IP covered by the server certificate | `127.0.0.1` |
| `OBSIDIAN_PORT` | Local REST API port | `27124` |
| `OBSIDIAN_PROTOCOL` | Connection protocol; use `https` | `https` |
| `REQUESTS_CA_BUNDLE` | Absolute path to the trusted certificate or PEM CA bundle | Requests' default trust store when unset |

TLS verification is enabled by default. For the plugin's self-signed certificate, configure `REQUESTS_CA_BUNDLE`; do not work around certificate errors by disabling verification. The ChatGPT gateway requires HTTPS and an explicit certificate file.

### TLS certificate setup

This certificate protects the connection from the bridge to **Obsidian Local REST API**. The OpenAI tunnel connection has its own TLS handling.

1. **Generate on the Obsidian side.** Enable Local REST API in the target Vault. The plugin generates its certificate material automatically on first use; no separate `openssl` generation command is needed for the default setup. In **Settings → Local REST API**, check that the encrypted HTTPS server is enabled.
2. **Save the public certificate.** Open the plugin's **Certificates** settings. If a **CA certificate** is present, copy its entire PEM block to a plain-text file such as `obsidian-ca.crt`. For the older single-certificate setup, copy **Certificate** (or the self-signed **Server certificate**) instead. Include the `BEGIN CERTIFICATE` and `END CERTIFICATE` lines. Store the file outside this repository at a stable path readable by the MCP process. Do not copy a private-key field or the plugin's complete `data.json`. Field names differ between [5.1](https://github.com/coddingtonbear/obsidian-local-rest-api/blob/5.1.0/src/main.ts) and [5.2](https://github.com/coddingtonbear/obsidian-local-rest-api/blob/5.2.0/src/main.ts).
3. **Configure trust in the client.** Set `REQUESTS_CA_BUNDLE` to that file's **absolute path**, using `env` in the ChatGPT gateway's `connection.json` or your local client's MCP configuration. Examples for both follow below. This explicitly configures trust for the Python Requests client; OS-wide Keychain or browser certificate registration is not required for these commands. See [Requests certificate verification](https://requests.readthedocs.io/en/stable/user/advanced/#ssl-cert-verification).
4. **Check TLS before connecting the AI client.** With Obsidian running, use the following command on macOS/Linux (requires `curl`), adjusting the certificate path and endpoint:

   ```sh
   curl --cacert "/absolute/path/to/obsidian-ca.crt" \
     --silent --show-error --output /dev/null \
     --write-out 'HTTP %{http_code}\n' \
     https://127.0.0.1:27124/
   ```

   An HTTP response without a TLS error confirms certificate and hostname verification. This request sends no API key and does not read notes; it does **not** verify API authorization. Test that separately with a read-only MCP call after completing Quickstart.

If verification fails, check the certificate file, expiry, and the endpoint's hostname/IP. To use an additional hostname, set **Certificate hostnames** in the plugin and use **Re-generate certificates**. Regeneration changes the trusted material: export the replacement and update every affected client's certificate file, then restart those clients and the gateway. **Reset all cryptography** also changes the API key, so it is not the certificate-only renewal action. If only a server certificate renews under an unchanged CA, clients can keep trusting that CA.

### ChatGPT gateway configuration

The gateway reads `connection.json` beside `chatgpt/gateway.py`. For a source installation, create it from [the example](chatgpt/connection.example.json), replace the placeholders locally, and keep it private:

```json
{
  "vault": "/absolute/path/to/vault",
  "write_folders": ["000_Inbox"],
  "env": {
    "OBSIDIAN_API_KEY": "REPLACE_LOCALLY",
    "OBSIDIAN_HOST": "127.0.0.1",
    "OBSIDIAN_PORT": "27124",
    "OBSIDIAN_PROTOCOL": "https",
    "REQUESTS_CA_BUNDLE": "/absolute/path/to/obsidian-ca.crt"
  }
}
```

`write_folders` replaces the default allowlist. Each entry is an exact top-level folder name; all of its subdirectories are included. Create the allowed folders in Obsidian before writing to them. Use `[]` to disable all writes. Omitting the setting enables the defaults `000_Inbox` and `020_Projects`.

This setting does **not** restrict reads: the gateway can read across the Vault. Restart the gateway process after configuration changes. See [gateway policy details](chatgpt/README.md#access-policy) for path restrictions and limits.

`connection.json` is ignored by Git; keep it mode `0600`. Keep API keys, certificates, tunnel profiles, and runtime files outside version control.

### Local client configuration

Supply the shared connection settings through your MCP client's environment configuration. A client using the `mcpServers` JSON format can use:

```json
{
  "mcpServers": {
    "mcp-obsidian-bridge": {
      "command": "/absolute/path/to/uv",
      "args": [
        "--directory", "/absolute/path/to/mcp-obsidian-bridge",
        "run", "--locked", "--no-sync", "mcp-obsidian"
      ],
      "env": {
        "OBSIDIAN_API_KEY": "REPLACE_LOCALLY",
        "OBSIDIAN_HOST": "127.0.0.1",
        "OBSIDIAN_PORT": "27124",
        "OBSIDIAN_PROTOCOL": "https",
        "REQUESTS_CA_BUNDLE": "/absolute/path/to/obsidian-ca.crt"
      }
    }
  }
}
```

Use your client's equivalent server settings if it uses another format. Find the `uv` executable with `command -v uv`.

The source repository is named `mcp-obsidian-bridge`, but the Python distribution and executable retain the upstream name `mcp-obsidian`. The command above runs **this checkout** after the installation step below. A bare `uvx mcp-obsidian` installs the upstream package instead of this fork.

For an existing shared installation, the [managed shared deployment runbook](docs/shared-deployment.md) describes commit-specific environments, a stable client launcher, and rollback.

Alternatively, the shared server loads connection variables from a local `.env` file. Keep it private and ignored by Git. The ChatGPT gateway uses its own `connection.json` configuration.

## Quickstart

### Install the shared prerequisites

1. Open the target Vault in Obsidian, enable Local REST API, obtain its API key, and complete [TLS certificate setup](#tls-certificate-setup).
2. Install Python, uv, and Git, then clone this fork and install its locked dependencies:

   ```sh
   git clone https://github.com/foreignlab/mcp-obsidian-bridge.git
   cd mcp-obsidian-bridge
   uv sync --locked --no-dev
   ```

Keep this checkout at a stable path: the following client commands refer to it. After updating the source, repeat `uv sync --locked --no-dev` and restart the affected MCP client or gateway.

### Connect ChatGPT

1. In the new checkout, create `chatgpt/connection.json` from the example. On macOS/Linux:

   ```sh
   umask 077
   cp chatgpt/connection.example.json chatgpt/connection.json
   chmod 600 chatgpt/connection.json
   ```

   Edit the file with the Vault path, Obsidian API key, certificate path, and intended write folders as described in [Configuration](#chatgpt-gateway-configuration).

2. Configure Secure MCP Tunnel using the [official setup guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels#set-up-tunnel-client). Set its **local stdio MCP command** to the gateway in this checkout:

   ```sh
   "/absolute/path/to/mcp-obsidian-bridge/.venv/bin/python" "/absolute/path/to/mcp-obsidian-bridge/chatgpt/gateway.py"
   ```

   This command waits for MCP messages on stdin; it is intended to be launched by the tunnel client. Use this gateway command, rather than the unrestricted `mcp-obsidian` command, for ChatGPT.

3. Start the tunnel client, then create a ChatGPT developer-mode app using the **Tunnel** connection and select your tunnel. Keep Obsidian and the tunnel client running.
4. In a ChatGPT conversation, select the app from the **+** menu (Developer mode), or type `@` and choose your app from the suggestions. For example, use `@obsidian` if you named the app `obsidian`; use your registered app's name if it differs. See [using connected apps](https://help.openai.com/en/articles/11487775-connected-apps-in-chatgpt).
5. Ask ChatGPT to list the Vault's root files or retrieve recent changes. If writes are enabled, try a disposable note in an allowed folder and verify the result in Obsidian before using existing notes.

This source-based setup does not install a background service. The [gateway operations guide](chatgpt/README.md) describes the maintainer's macOS service setup; the [deployment runbook](docs/deployment.md) covers updating and rolling back that existing installation. `chatgpt/launch.sh` is a historical launcher with machine-specific paths, not a fresh-install launcher.

### Connect a local MCP client

1. Add the [local client configuration](#local-client-configuration) to your client's MCP settings, using absolute paths to `uv`, this checkout, and your certificate file.
2. Restart or reconnect the client so it launches `mcp-obsidian` from this checkout.
3. Ask it to list the Vault's root files, then read a known note. Obsidian must remain running.

This entry point uses the shared server directly and does not apply the ChatGPT gateway's tool allowlist or write-folder restrictions.

## Security model

The ChatGPT connection uses this path:

```text
ChatGPT → OpenAI Secure MCP Tunnel → restricted gateway → HTTPS Local REST API → Vault
```

The local client path is:

```text
Local MCP client → shared stdio server → HTTPS Local REST API → Vault
```

The fork verifies TLS certificates and adds a gateway policy for remote tool calls: an explicit tool allowlist, argument validation, write-folder restrictions, and rejection of traversal and symbolic links. Deletion through the gateway is limited to individual files. Diagnostic logs omit note contents and credentials; see [diagnostics](docs/diagnostics.md) for their scope.

The gateway does not provide a read-folder allowlist or reduce the permissions of the Obsidian REST API key. Anyone holding that key can bypass the gateway; the local account, Obsidian, and its plugins remain trusted. Treat notes returned to an AI client as untrusted input and review destructive actions. The operator remains responsible for access to the tunnel, secrets, and Vault backups. Passing tests does not establish that every deployment is secure.

Keep the Local REST API private. The documented ChatGPT path uses Secure MCP Tunnel; this fork does not provide a public HTTP MCP endpoint or a Cloudflare Tunnel configuration. Other transports would require their own integration and access-control validation.

## Compatibility and differences from upstream

- **TLS verification** is enabled by default in the shared client.
- **The ChatGPT gateway** adds the tool and path policy described above.
- **PATCH** selects legacy format 1 explicitly with `Markdown-Patch-Version: 1` for Local REST API 5.x. This does not implement PATCH format 2.
- **Recent Changes** uses a fixed JSONLogic query instead of DQL. It selects Markdown notes modified since local midnight `days` calendar days ago, inclusive, in the MCP process's timezone, then sorts by modification time descending and filename for ties before applying `limit`. Run it in Obsidian's timezone for matching day boundaries.
- **Diagnostics and deployment tooling** support the separate ChatGPT runtime; editing a source checkout does not update an existing managed installation.

PATCH and Recent Changes have been exercised against Local REST API **5.1.0**. This is a verification point, not a claim of compatibility with every plugin version. The legacy periodic-note tools are not a supported daily-note workflow on versions without `/periodic/` endpoints.

See [fork maintenance](docs/upstream/maintenance.md) for source history and upstream updates, [investigation records](docs/investigations/) for compatibility findings, and [deployment records](docs/deployments/) for historical validation. The [preserved upstream README](docs/upstream/README.md) records the original documentation; its installation examples target upstream.

## Development

For local development or your own fork, run the full test suite on **macOS or Linux**, with the **`openssl` command** available on `PATH` for the TLS fixtures. Install the development dependencies and run:

```sh
uv sync --locked --group dev
uv run --locked --no-sync pytest -q
```

Tests use mocks, temporary files, and local HTTP/HTTPS fixtures. They require loopback socket access and do not write to a real Vault. Live REST API compatibility and the complete ChatGPT connection require separate checks.

Report bugs and suggest improvements in [this fork's Issues](https://github.com/foreignlab/mcp-obsidian-bridge/issues). Include the affected client, plugin version, and a reproducible example with secrets and private note contents removed. **External pull requests are not accepted.** Maintainer changes still use pull requests for CI and review. This project is maintained for personal use, without a general support commitment.

## License and attribution

Based on [MarkusPfundstein/mcp-obsidian](https://github.com/MarkusPfundstein/mcp-obsidian). The original project's [MIT license](LICENSE) and attribution are retained.
