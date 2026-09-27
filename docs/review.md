# Review guide

This guide applies to local and GitHub reviews, and to both agents producing or
receiving findings. Review against the task's intended behavior and the actual
deployment: personal-use infrastructure that can read and modify a real Vault,
with a public repository and a restricted gateway for ChatGPT.

## What matters

For code, prioritize reachable failures: wrong or lost note content, unauthorized
writes, TLS verification regressions, disclosure of secrets or private data,
broken tools, and checks that can report success without verifying the outcome.
Check the affected path: a shared-client fix may affect several clients, while
gateway authorization and logging guarantees belong to that entry point.
Do not assume the gateway protects against a compromised local account or plugin.

For documentation, check whether someone following it would perform the intended
action against the intended target. Report broken commands, missing essential
prerequisites, false descriptions of current behavior, and contradictions that
change what a reader does. Distinguish current behavior from proposed behavior;
a proposal's difference from today's code is not itself a defect.

Each finding should identify the location, triggering situation, evidence, and
consequence. Separate defects requiring correction from optional improvements.
Stylistic preference, exhaustive coverage, and hypothetical enterprise requirements
alone are not reasons to hold up this project's changes.

## Review and correction

- The initial local review covers the whole change and reports findings together.
  Give the reviewer the task, exact worktree, base, and whether changes are committed
  or still in the working tree. Request review without editing the author's files.
- Verify a finding against the implementation or procedure before changing anything.
  Severity labels are input, not proof. If the reasoning is wrong, explain the
  evidence; if it identifies a real defect, correct that defect.
- For prose, prefer replacement or removal over appended qualifications. Preserve
  requirements that affect the reader's actions. Repeated findings on the same
  passage call for reconsidering its premise or structure.
- A follow-up local review checks the accepted fixes and their effects. Do not
  commission another full review merely to seek more wording suggestions. Newly
  discovered material defects still need a disposition, including missed defects
  and regressions introduced by fixes.
- A valid but separate concern belongs in a linked issue when deferred. Explain
  the disposition in the review thread; resolve it only after verifying the fix
  or documenting the agreed deferral. Do not reopen a considered trade-off without
  new evidence. Use the `codex-review-gate` skill for bot-reaction policy.

GitHub's automatic reviewer may review more than the latest fix. Apply these
criteria when receiving its findings; do not claim to control its review scope.
If corrections keep producing new findings on the same passage, stop adding
clauses and show the user the remaining concrete consequences and proposed
disposition before starting another rewrite. This is a decision point, not an
automatic waiver of the [merge requirements](development.md#review-pr-and-merge).

## Instruction files

AGENTS.md, CLAUDE.md, and the procedural guides they reference receive independent
local review like other documentation. Check that shared rules are available to
both agents, Claude-specific duties stay separate, and links tell readers when
to load the relevant guide. Keep entry files short and each rule in one place.
The ordinary CI and latest-head review requirements remain in force; changing
review instructions does not exempt the change from review.
