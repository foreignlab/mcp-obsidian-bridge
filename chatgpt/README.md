# Obsidian MCP for ChatGPT

This dedicated stdio entry point uses the TLS-verified local `mcp-obsidian`
copy without changing Codex, Claude Code, Hermes, or n8n configuration.

## Access policy

- Read access covers every Vault folder through file reads, batch reads,
  directory listings, simple/tag search, frontmatter, and recent changes.
- Create, replace, append, patch, and single-file deletion are limited to
  `000_Inbox/` and `020_Projects/`, including their subdirectories.
- Deletion requires the JSON boolean `true`; recursive directory deletion
  is not exposed. Replacement and deletion have destructive tool annotations.
- Hidden write paths, traversal, absolute paths, symbolic links, and hard-linked
  write targets are rejected before invoking the REST client.
- Percent signs, colons, backslashes, control characters, and ambiguous path
  components are rejected. This means filenames containing those characters
  and symlinked notes cannot be accessed through this entry point.
- URL metacharacters such as `#` and `?` in supported filenames are encoded.
- Unreviewed tools are denied. Arbitrary JSONLogic search and periodic-note
  tools are not exposed; their notes can still be read by file path.
- JSON argument schemas are enforced. Content is limited to 1,000,000
  characters per write, and batch reads to 50 files.

## Configuration and startup

`connection.json` holds the Vault path and the Obsidian client environment.
It is private (mode 0600); never attach it to a report or commit it to Git.
The runtime checks that its API key matches that Vault's Local REST API
settings and that HTTPS, a trusted certificate file, and TLS verification
are configured before starting the stdio server.

Use the absolute path to `launch.sh` as the Secure MCP Tunnel MCP command.
The launcher is connected to the `obsidian-chatgpt` Secure MCP Tunnel.
The runtime key is stored in the private `tunnel/runtime-api-key` file and
referenced by the profile. Never paste the runtime API key into chat.

## macOS automatic startup

The user LaunchAgent at
`~/Library/LaunchAgents/com.foreignlab.obsidian-chatgpt-tunnel.plist`
runs `tunnel-client run` on GUI login and restarts it after process exit.
The restart throttle is 30 seconds. The previous tmux-managed runtime was stopped.
Do not use `tunnel-client runtimes connect` while this LaunchAgent is active;
it can start a duplicate runtime.

The initial launch and automatic recovery after SIGTERM both passed health
checks requiring a successful OpenAI control-plane poll. An actual Mac reboot
was not performed. Obsidian must also be running for Vault operations;
its own login-item settings were not changed.

Use `launchctl print gui/$(id -u)/com.foreignlab.obsidian-chatgpt-tunnel`
to inspect the service. Use `launchctl bootout` with that service target to
stop it for the current login session, `launchctl bootstrap gui/$(id -u)`
with the plist path to start it again, or `launchctl kickstart -k` with the
service target to restart it. Persistent disable/enable uses `launchctl
disable`/`enable` for the same service target.

Health URL and PID files are `tunnel/launchd-health.url` and `tunnel/launchd.pid`.
Probe using `tunnel-client health --url-file <health-file> --pid-file <pid-file>
--require-control-plane-poll --json`. Runtime logs are in
`tunnel/launchd-runtime.log`; launchd output is in `tunnel/launchd.stdout.log`
and `tunnel/launchd.stderr.log`.

The source gateway now emits content-free JSON diagnostics to stderr: startup,
MCP initialization, correlated tool calls, and classified REST failures. See
[the diagnostics runbook](../docs/diagnostics.md) for fields and incident
triage. This addition is not yet deployed; verify stderr forwarding and
configure log retention when deploying it.

The full Japanese runbook is in the Vault:
`000_Inbox/2026-09-20 Obsidian MCP Tunnel 設定・検証結果.md`.

Official setup: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
Management: https://platform.openai.com/settings/organization/tunnels

## Validation and boundaries

129 tests cover policy and tool dispatch. Integration tests use a temporary
HTTP fixture and temporary files; no real Vault writes are performed by them.
The shared TLS client previously passed 84 tests including certificate rejection.

This is application-level authorization for ChatGPT tool calls, not a
filesystem sandbox or a reduced-scope Obsidian REST API credential. A holder
of the REST API key can bypass this entry point. The local account, Obsidian,
and the plugins remain trusted. A hostile local process could swap a filesystem
entry between the path check and the REST operation; these are separate
processes and the check is not atomic. This gateway cannot protect against a
compromised local account or malicious Obsidian plugin.

The TLS certificate expires on 2027-09-20 12:34:26 UTC. Maintain this entry
point together with the pinned local TLS client when dependencies or the
Obsidian API change. Retest before exposing additional tools.
