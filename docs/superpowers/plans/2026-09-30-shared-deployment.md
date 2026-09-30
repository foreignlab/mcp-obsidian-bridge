# Managed Shared-Client Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare and verify committed shared-server environments, select them through a stable launcher, and restore the previous route after failure.

**Architecture:** A standard-library connection reader supplies only the selected client's environment to a read-only MCP probe. A separate deployment manager archives and builds commit-specific environments, validates installed package contents, and performs journaled pointer transitions. Client adoption is documented and performed separately.

**Tech Stack:** Python 3.11+, standard library, Git, uv 0.12.16, MCP Python SDK 1.29.0, existing pytest fixtures.

**Spec:** [Managed shared-client deployment](../specs/2026-09-30-shared-deployment-design.md).

## Global Constraints

- Support macOS and Linux with Python 3.11+, Git, and uv. Add no package dependency.
- Use the source commit's `uv.lock`; retain the existing MCP and Requests pins.
- Use the installed SDK's v1 interfaces, even when current documentation includes v2 examples.
- Default runtime root: `~/.local/share/mcp-obsidian-tls/`; keep legacy source, metadata, certificates, and private configuration in place.
- New control directories are mode 0700; private metadata and history are 0600; the launcher is executable only by its owner.
- Virtual environments are built at their final path and never moved after installation.
- Build with `uv sync --locked --offline --no-dev --no-editable` using the selected Python.
- Connection environment values stay out of release manifests, history, Git, and public output.
- A pending transition blocks other mutations until recovery. No probe writes to the Vault.
- The manager never edits client configuration or stops other sessions. Real-runtime adoption and source-release publication require separate authorization.

## Review Focus

1. An adopted client entry points at the new launcher: preserve the frozen legacy command and read only its current connection environment (Tasks 1 and 2).
2. The legacy entry has no `cwd`: require an explicit initial `--legacy-cwd`; do not guess a host's startup directory (Task 2).
3. Archived source matches but installed Python files are stale or extra: reject activation based on installed package inventory, too (Task 2).
4. A process dies after writing pointer or state but before clearing the journal: restore the snapshot, including an originally absent launcher (Task 3).
5. An SDK error includes configuration values or note text, or ambient env redirects startup: suppress captured output and run only the selected profile/installed environment (Tasks 1 and 3).

## Files and responsibilities

| File | Responsibility |
| --- | --- |
| `scripts/shared_connection.py` | Typed client profile, fixed errors, TOML/JSON reader, connection validation, clean subprocess environment. |
| `scripts/probe_shared.py` | SDK-dependent read-only probe and its subprocess CLI. |
| `scripts/deploy_shared.py` | SDK-independent preparation, legacy record, launcher, manifests, state/journal transitions, operator CLI. |
| `tests/test_shared_connection.py` | Profile types, TLS settings, explicit env, fixed errors. |
| `tests/test_shared_probe.py` | Tool catalog/result validation, timeouts, error privacy, synthetic stdio path. |
| `tests/test_shared_deployment.py` | Temporary Git/runtime fixtures, preparation, integrity, transitions, interruption recovery. |
| `docs/shared-deployment.md` | Initial adoption, updates, rollback, recovery, offline cache prerequisites and verification limits. |
| `AGENTS.md`, `README.md`, `docs/development.md`, `docs/deployment.md` | Small links to the owning shared deployment guide. |

Keep the ChatGPT deployment implementation and gateway policy unchanged. The source-release policy is on the separate `docs/release-policy` branch; this implementation must not require its unmerged files.

## Task 1: Private client profiles and read-only MCP verification

**Files:** Create `scripts/shared_connection.py`, `scripts/probe_shared.py`, `tests/test_shared_connection.py`, `tests/test_shared_probe.py`.

**Interfaces:**
- `SharedRuntimeError(Exception)`: fixed public messages; never interpolate private configuration or tool results.
- `ClientProfile` dataclass: `command: str`, `args: list[str]`, `env: dict[str, str]`, `cwd: Path | None`. Exclude `env` from repr.
- `read_client_profile(path: Path, server: str) -> ClientProfile`: accept TOML `mcp_servers` and JSON `mcpServers`; validate only the named entry, HTTPS, required connection variables, and a readable CA file.
- `clean_environment(connection: dict[str, str]) -> dict[str, str]`: retain required system execution variables, remove ambient `OBSIDIAN_*`, `REQUESTS_CA_BUNDLE`, `UV_*`, `VIRTUAL_ENV`, `PYTHONPATH`, and `PYTHONHOME`, then overlay validated selected-entry values. Reserved Python/uv/virtualenv override keys remain removed even when present in the profile.
- `async probe(profile: ClientProfile, *, command: list[str], cwd: Path) -> dict`: return `ok`, `tools`, `vault_entries`, and `recent_changes` counts.
- Probe `main(argv: list[str] | None = None) -> int`: required `--client-config`/`--server`; exactly one of `--executable PATH --cwd PATH`, `--launcher PATH`, or `--launch-record PATH`. Records contain only legacy command/args/cwd; connection values come from the selected profile.

- [ ] **Step 1: Write profile/probe regression tests.** Use sentinel secrets and synthetic note text; fake SDK results for failure cases and a synthetic stdio MCP server for the successful transport path. Exact contracts include:

```python
def test_named_profile_overrides_ambient_connection(profile_file, monkeypatch):
    monkeypatch.setenv('OBSIDIAN_API_KEY', 'wrong-ambient-key')
    profile = read_client_profile(profile_file, 'obsidian')
    env = clean_environment(profile.env)
    assert env['OBSIDIAN_API_KEY'] == 'selected-sentinel-key'
    assert 'selected-sentinel-key' not in repr(profile)
    assert 'PYTHONPATH' not in env and 'UV_PROJECT_ENVIRONMENT' not in env
```

Parameterize both configuration formats, missing server, malformed field types, HTTP, missing CA, missing key, and absent `cwd`. For probe tests require exactly the 15 shared tools currently registered in `src/mcp_obsidian/server.py`, reject duplicates/missing/extra tools, and require only listing and Recent Changes calls. Reuse `probe_gateway.validate_recent`'s boundary/order validation without changing the gateway. Check empty successful results, malformed JSON/structure, `isError`, timeout, and nested SDK exceptions; captured output must contain no sentinel key, synthetic path/content, or raw child stderr.

- [ ] **Step 2: Run focused tests and confirm the missing implementation fails.** Run `uv run --locked --no-sync pytest -q tests/test_shared_connection.py tests/test_shared_probe.py`.
- [ ] **Step 3: Implement the interfaces above.** Use `ClientSession` with a 15-second read timeout and an overall 45-second timeout; capture stderr in a private temporary stream. Derive subprocess connection values only from the selected entry. Report coarse errors and nonzero CLI status on any failed check.
- [ ] **Step 4: Repeat the focused command; require every case to pass.** Run the synthetic transport test through a real subprocess, not only mocked SDK calls.
- [ ] **Step 5: Commit the four files.** Message: `feat: add private shared-client profiles and read-only probe`.

## Task 2: Commit-specific preparation, provenance, and legacy route

**Files:** Create `scripts/deploy_shared.py` and `tests/test_shared_deployment.py`; consume Task 1 modules.

**Interfaces:**
- `SharedDeployment(root: Path, repo: Path, *, client_config: Path | None = None, server: str | None = None, uv: str | None = None, python: str | None = None, legacy_cwd: Path | None = None)`: default uv from PATH and Python from `sys.executable`.
- `prepare(revision: str) -> str`: return a full prepared source SHA.
- `_manifest(revision: str, *, require_prepared: bool = True) -> tuple[Path, dict]`: source inventory/hash/mode verification and installed package provenance.
- `_probe(target: str, *, launcher: bool = False, probe_revision: str | None = None) -> dict`: use a release's `.venv/bin/python` and copied probe script, never require MCP in the operator interpreter. `target` is a full SHA or `legacy`; `probe_revision` supplies the environment for legacy/recovery probes.
- `_launcher() -> bytes`: shell script that resolves `current` once, execs its absolute installed entry point, or executes the private recorded legacy command in its recorded directory.
- `status() -> dict`: read-only JSON with `selected`, `previous`, `pending`, and `integrity` (a coarse status, not private paths). Initial state is selected `legacy`, previous `None`.
- Persist source manifest `{revision, status, files, prepared_at, probe}`; files map names to `{sha256, mode}`. Preserve legacy `{command, args, cwd, files}` privately and never replace it after client adoption.

- [ ] **Step 1: Add preparation/provenance regressions with temporary repositories and injected builds.** Pin these outcomes:

```python
def test_prepare_preserves_legacy_and_does_not_select_candidate(shared_runtime):
    app, legacy_bytes = shared_runtime
    revision = app.prepare('HEAD')
    assert app.status()['selected'] == 'legacy'
    assert not (app.root / 'current').exists()
    assert not (app.root / 'launch.sh').exists()
    assert legacy_bytes == snapshot_legacy_sources(app.root)
    assert 'selected-sentinel-key' not in public_manifests(app.root)
    assert app.prepare(revision) == revision
```

Test tag resolution, dirty/untracked source rejection, unsafe archive entries (traversal, links, private runtime files), interrupted extraction/retry, failed build/probe with no route change, incomplete activation rejection, source tampering, stale/missing/extra installed package files, and inherited build overrides. Require explicit legacy cwd when neither profile nor operator supplies it. After updating the synthetic client entry to `launch.sh`, preparation must retain the original legacy argv/cwd and read fresh connection values. Check metadata modes, build-at-final-path, exact build flags, and no changes to legacy source or ChatGPT fixtures.

- [ ] **Step 2: Run `uv run --locked --no-sync pytest -q tests/test_shared_deployment.py`; confirm failures before implementation.**
- [ ] **Step 3: Implement preparation and read-only status.** Use `git rev-parse` with option termination and validate full SHA before archival. Stage the safe archive and manifest together, then build the final path; mark prepared only after installed-file identity and probe pass. Verify installed `mcp_obsidian` files by reading distribution metadata using the release interpreter without importing the server, and compare every package Python file to committed source. Capture raw build output and return fixed errors. Capture the legacy entry once, resolve an explicit cwd, and preserve its source/package metadata inventory.
- [ ] **Step 4: Run preparation tests plus Task 1 tests; require all to pass.** Also exercise generated launchers using fake executable releases at paths containing spaces and verify no protocol stdout is added.
- [ ] **Step 5: Commit the manager and tests.** Message: `feat: prepare verified shared-server environments by commit`.

## Task 3: Journaled switching, CLI, and adoption runbook

**Files:** Extend `scripts/deploy_shared.py`, `tests/test_shared_deployment.py`; create `docs/shared-deployment.md`; add guide links in `AGENTS.md`, `README.md`, `docs/development.md`, `docs/deployment.md`.

**Interfaces:**
- `activate(revision: str) -> None`: require a full prepared SHA, validate/probe target and selected route, journal then switch and verify installed launcher.
- `rollback() -> None`: transition to recorded previous SHA or legacy, retaining the stable launcher after successful adoption.
- `recover() -> None`: restore the pending snapshot, probe it through a retained release environment, and clear pending only after verification.
- `_transition(target: str) -> None`: common preflight, snapshot, mutation, postflight, state/history commit, or restoration-on-failure.
- `main(argv: list[str] | None = None) -> int`: commands `prepare REVISION`, `activate FULL_SHA`, `rollback`, `recover`, `status`, `probe`; path/tool/config overrides precede the command. Every operation requiring a connection requires `--client-config` and `--server`; `status` does not.
- State `{selected, previous}`; pending `{state, target, launcher, probe_revision}` preserves prior launcher existence/content/mode and pointer absence/target. Store launcher content safely in private JSON or a referenced private snapshot file. History contains timestamps, fixed event names, full SHAs/legacy, and coarse outcomes only.

- [ ] **Step 1: Add transition and CLI regression tests.** Assert these recovery contracts:

```python
def test_failed_first_activation_restores_absent_launcher(shared_runtime, monkeypatch):
    app, _ = shared_runtime
    revision = app.prepare('HEAD')
    fail_installed_launcher_probe(app, monkeypatch)
    with pytest.raises(SharedRuntimeError, match='restored'):
        app.activate(revision)
    assert app.status()['selected'] == 'legacy'
    assert app.status()['pending'] is False
    assert not (app.root / 'launch.sh').exists()
    assert not (app.root / 'current').exists()
```

Add success for first activation, legacy rollback, second managed revision/rollback, and actual launcher execution in each route. Test no previous target, modified legacy inventory, modified pointer/launcher, preflight failure, post-switch failure, and recovery-probe failure retaining the journal. Inject `KeyboardInterrupt` at pointer write, state write, history write, and pending removal; a subsequent `recover` must restore the snapshot without overwriting it. Exercise lock contention and pending-state refusal for preparation/activation/rollback. Ensure launcher processes keep the release resolved at startup when a pointer changes, ignore hostile Python/virtualenv/uv overrides in the managed route, and leave older client processes running. CLI tests require exact nonzero failure statuses and no sentinel data in stdout/stderr.

- [ ] **Step 2: Run all three focused test modules and confirm new failures before implementation.**
- [ ] **Step 3: Implement transitions and CLI.** Use a nonblocking exclusive file lock for mutations, owner-private atomic metadata, candidate/legacy preflight, a snapshot durable before pointer mutation, launcher postflight, and restoration with journal retention on failure. Report failed activation even after successful restoration. Do not turn process interruption into a success. Keep status/probe read-only and distinguish selected revision from connected client versions.
- [ ] **Step 4: Write the runbook and guide links.** Include explicit config/server/cwd arguments, source-tag-to-SHA resolution, offline cache setup before preparation, preparation at a reviewed commit, activation JSON checks, a synthetic client entry pointing at absolute `launch.sh` with retained env, reconnection per client, standalone probe, legacy rollback limitation, recovery, and sanitized deployment records. Document separately authorized installation and that launcher probing does not prove every client reconnected. Validate CLI examples with `--help` and synthetic config; validate local links and anchors.
- [ ] **Step 5: Run focused tests, then `uv sync --locked --group dev` and `uv run --locked --no-sync pytest -q` in this worktree.** Require the full suite to pass; default tests must use no real Vault or private configuration. Stage intended files; require `git diff --check`, `git diff --cached --check`, and `git diff --check origin/main...HEAD` to succeed. Perform one independent whole-change local review and address concrete findings before first push.
- [ ] **Step 6: Commit the final task.** Message: `feat: add recoverable shared-client switching and adoption guide`. Report commits, verification, review, and production changes (none during implementation). Follow the repository's issue/PR/review gates for integration; authorize merging and real deployment separately.

## Execution handoff

Recommended method: native execution in this session, followed by an independent
whole-branch reviewer. The three tasks share one lifecycle and fixture set;
keeping their implementation together reduces handoff overhead. Tests explicitly
cover failure and interruption boundaries before final review.

The maintainer reviews this plan and selects native or subagent-driven execution
before product-code implementation. The approved spec and this plan remain the
contract for either execution method.
