# Claude orchestration

Read this when Claude coordinates work in this repository. Common requirements
remain in [development](development.md) and [review](review.md); Codex working
directly with the user follows those guides without requiring a Claude session.

## Responsibilities

Claude owns the user conversation, scope and task breakdown, delegation, acceptance
of the result, and PR follow-through. Delegate implementation and its tests to
Codex when the task has a clear scope and completion criteria. Claude reviews the
actual result and verification evidence before accepting it.

Claude normally authors policy, design, and explanatory documentation, with Codex
providing implementation checks and independent review. The user may assign a
different division of work. Do not renegotiate the default on every task.

## Delegation

Use the `codex-delegation` skill before launching or supervising delegated Codex
work. Its implementation path is for changes to the tree; its review path is for
independent findings. The skill owns launch and supervision mechanics; do not
copy its commands or model settings into repository rules. Model and effort choices
come from the user or an ignored `CLAUDE.local.md`, not tracked documentation.
If a required delegation tool or setting is missing, report that limitation and
agree on an alternative instead of silently claiming delegation or independence.

Keep the coordinating session in the primary checkout and start the delegate in
the task worktree, as specified in the development guide. Avoid Claude mechanisms
that move the coordinating session into another checkout; operate on explicit
worktree paths instead.

Give each task a brief containing:

- The intended result and references to the agreed design or issue.
- The exact worktree, branch, owned files or responsibility, and relevant constraints.
- Completion criteria and required verification, including any live checks still needed.
- Authority and stop conditions for commits, pushes, PR creation, and production work.

Tell delegates that other agents may be working in the repository and that they
must preserve others' edits. Keep ownership clear and avoid concurrent writers
to the same worktree. Supervise until the run finishes or a concrete blocker is
reported; answer questions within the user's existing authorization.

## Accepting work and following the PR

Inspect the actual diff, confirm the worktree and revision it represents, and
check the verification output. A delegate's completion summary alone is not
verification. Ensure the independent local review required by the development
guide covers the resulting changes; the implementing agent's self-review does
not replace it. Use the review guide's focused follow-up process for fixes.

Claude is the single PR monitor for work it orchestrates. Use `claude-mem:babysit`
and `codex-review-gate` for each PR, retain responsibility for deciding how to
handle findings, and delegate concrete code fixes as needed. Do not have Claude
and Codex independently pushing review fixes to the same branch. Report the final
revision, verification results, unresolved limits, and any production changes.
