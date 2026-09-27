# Release deployment implementation plan

**Goal:** Deploy the committed ChatGPT gateway independently of shared MCP clients, with verified preparation and automatic recovery after failed activation.

**Architecture:** Archive a clean Git commit into a permanent release directory and install its locked runtime dependencies there. Preserve the original launcher; atomically switch a managed launcher/current pointer only after a read-only stdio probe. Serialize deployment commands and roll back if restart or health checks fail.

**Spec:** The deployment sequence approved in this conversation: prepare, validate, switch/restart, verify, and restore the previous launcher on failure.

**Constraints:** Preserve live credentials, certificates, Tunnel profile, and shared mcp-obsidian-tls. Never write to the real Vault during verification. Never relocate an installed virtual environment. Use English commits. Keep existing Tunnel logs; bound new diagnostic logs to 10 MiB plus five backups.

## Tasks

- [x] Add optional process-safe bounded diagnostic storage (`chatgpt/log_store.py`); test rotation, permissions, fallback, and concurrent writers. Keep stderr as the default.
- [x] Add a reusable stdio probe (`scripts/probe_gateway.py`) that initializes MCP, lists tools, lists the Vault, and checks Recent Changes without printing returned paths/content.
- [x] Add deployment commands (`scripts/deploy.py`): prepare, activate, rollback, status. Reject dirty sources and invalid releases; preserve legacy launcher; journal transitions and auto-restore after failed health checks.
- [x] Test with temporary Git repositories and fake service callbacks: dirty tree, unsafe archive members, incomplete release, preflight failure, health failure, successful switching, legacy rollback, repeated prepare, and concurrent deployment lock.
- [x] Document deployment, first rollback, subsequent rollback, log retention, and interrupted-transition recovery in `docs/deployment.md`.
- [x] Run the full suite and commit the deployable source.
- [x] Prepare the committed release in the production root; run the read-only probe before any restart.
- [x] Activate, verify Tunnel health and the installed launcher, then check the connected Obsidian app if available. Record any end-to-end verification that remains unavailable.

## Review focus

- A virtual environment created in a staging path must never be moved: prepare at its final commit path, and gate activation on a complete manifest.
- A failed restart must restore both the launcher and current pointer, and verify recovery before reporting it as successful.
- A partial or modified release must never become current: verify hashes and require a successful probe.
- A second deployer must not overwrite rollback state: hold a nonblocking process lock through each transition.
- A log rotation from one gateway must not leave another writer appending to an unlinked file: acquire a shared file lock and open the current file on each write.

## Pre-deployment verification

- Full regression suite: 272 passed on 2026-09-22.
- Independent review found and rechecked fixes for launcher probes using an unprepared interpreter and interrupted source extraction blocking retries. Two regression tests reproduce both cases.
- Production preparation, activation, installed-launcher verification, and the remote connector call completed successfully. See [the deployment record](../../deployments/2026-09-22.md).
