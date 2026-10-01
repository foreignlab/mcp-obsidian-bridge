# Development workflow

This guide applies to every implementing or coordinating agent, including Codex
working directly with the user. Claude's delegation duties are in
[Claude orchestration](claude-orchestration.md).

## Scope and worktrees

Ground the task in its issue and the user's instructions. For a change that needs
tracking, use an issue in `foreignlab/mcp-obsidian-bridge`; a bounded follow-up can
stay with its existing issue. Agree on behavior before a substantial design change.
Keep implementation and documentation changes focused on that scope.

Work that creates a branch or commit uses a dedicated worktree under `.worktrees/`
(already ignored). Check `git status` and `git worktree list` first; reuse a worktree
already assigned to the task. Never switch another agent's branch or discard its edits.
The coordinating session stays in the primary checkout and operates on the worktree
by explicit path. Start a delegated agent in the assigned worktree.

For a new task, from the primary checkout:

```sh
git fetch origin
git worktree add .worktrees/TASK -b TYPE/TASK origin/main
```

Replace `TASK` and `TYPE/TASK` with unused task and branch names. Run the commands
below in that worktree, using the tool's working-directory argument or `git -C`
for Git commands. Before review, verify the worktree path, branch, base, and complete
change set, including staged, unstaged, and untracked files.

## Verification

Development is supported on macOS and Linux with Python 3.11+, `uv`, and `openssl`
on `PATH`. Windows is outside the tested scope. Install and test with:

```sh
uv sync --locked --group dev
uv run --locked --no-sync pytest -q
```

Before review, stage intended new files so they participate in the index check.
Check unstaged changes, the index, and the committed branch diff separately:

```sh
git diff --check
git diff --cached --check
git diff --check origin/main...HEAD
```

For code, dependencies, or test changes, run focused tests while working and the
full suite before opening the PR. Add regression coverage that reproduces a bug
before its fix. The suite uses temporary files, mocks, and loopback HTTP/HTTPS
fixtures; it requires local socket access, not a real Vault or production credentials.
Do not add live-service dependencies to the default suite.

For documentation-only changes, verify commands, examples, links, and claims
against their source and apply [the review guide](review.md). Python tests do not
verify prose; a local full-suite rerun is unnecessary when executable files are
unchanged. CI still runs for documentation PRs.

When compatibility with the real REST plugin needs checking, record its version,
the client path exercised, and the result. Use disposable notes in an allowed
folder for authorized write checks, verify the result and cleanup, and never use
existing personal notes as fixtures. Report a missing live check as unverified;
passing mocks does not establish live compatibility.

## Review, PR, and merge

1. Obtain an independent local review before the first push and handle findings
   using [the review guide](review.md). Review instruction-file changes against
   the user's agreed design, not just the rules those changes introduce.
2. Commit focused changes and open the PR against `foreignlab/mcp-obsidian-bridge`
   `main`. Summarize resulting behavior, verification, and material limits; link
   the issue. Never post or push upstream without an explicit request.
3. Assign one PR monitor. Use `claude-mem:babysit` to follow checks and findings,
   and `codex-review-gate` to determine review completion. A fix push requires a
   fresh completion signal for its head. Read review bodies as well as threads.
4. Before an authorized merge, require all three test checks to succeed: Ubuntu
   Python 3.11 and 3.13, and macOS Python 3.13. Confirm the latest head's Codex
   review is complete and no actionable findings or unresolved threads remain.
   Use the gate skill's script; if unavailable, report the missing gate rather
   than substituting an improvised detector. These requirements also apply to
   documentation and instruction-file PRs.
5. Merge with a merge commit and `--match-head-commit` set to the verified full
   head SHA. Confirm the result, fast-forward the primary `main` only if clean,
   and check the resulting main CI. Preserve published history.

Follow the authorization already given for the task; review success is evidence
of readiness, not additional authority to publish, merge, or change production.
Leave task branches and worktrees in place until cleanup is requested or included
in the task. Before removing one, verify it contains no unmerged or uncommitted work.

## Deployment and upstream work

For source tags and GitHub Releases, follow the [release policy](releases.md).
Source releases use publication dates; runtime deployments record exact commits.

Merge and deployment are separate operations. For authorized ChatGPT deployment,
use [the deployment runbook](deployment.md); do not hand-edit prepared releases or
runtime pointers. That runbook does not update the shared-client installation.
If both clients need a fix, identify and verify each deployment separately.
For shared stdio updates and initial launcher adoption, use the
[shared deployment runbook](shared-deployment.md).

Use [fork maintenance](upstream/maintenance.md) when importing upstream changes.
Keep upstream-compatible fixes distinct from gateway and deployment policy.
For diagnosis, use [the diagnostics runbook](diagnostics.md) and synthetic examples
instead of copying private configuration, raw logs, or note content into GitHub.
