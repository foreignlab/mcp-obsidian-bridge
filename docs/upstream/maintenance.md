# Fork maintenance

## Repository boundaries

- `origin`: https://github.com/foreignlab/obsidian-chatgpt-mcp
- `upstream`: https://github.com/MarkusPfundstein/mcp-obsidian
- `main`: reviewed downstream source, including upstream ancestry.
- `archive/pre-fork-20260927`: original local tip, kept for historical reference.

`src/mcp_obsidian/` is the shared client. Its downstream changes enable TLS
verification by default, replace Recent Changes DQL with JSONLogic, and add
content-free request failure observations. `tools.py` also documents the updated
Recent Changes behavior. Keep fixes that could be contributed upstream in
focused commits and track matching upstream issues or PRs before creating new ones.

`chatgpt/` owns the restricted gateway, write-folder policy, and bounded logs.
`scripts/` owns release preparation, activation, rollback, and probes. These
features serve this deployment and are maintained here. The shared client and
ChatGPT runtime are separate installations; updating this checkout changes neither.

The fork currently retains the pre-migration file inventory. Upstream Docker
packaging, its CI workflow, and the historical `openapi.yaml` are absent. Changes
to those files in later upstream updates need an explicit integration decision.

## Updating from upstream

Start from a clean `main`, fetch `origin` and `upstream`, and create an update
branch. Inspect the incoming changes before merging `upstream/main` on that
branch; resolve conflicts while retaining the downstream behavior described above.
Do not rebase or rewrite published commits: deployed release IDs and the archive
branch remain valid history.

```sh
git fetch origin
git fetch upstream
git switch main
git pull --ff-only origin main
git switch -c maintenance/upstream-YYYY-MM-DD
git log --oneline HEAD..upstream/main
git diff HEAD...upstream/main
git merge upstream/main
uv sync --locked --group dev
uv run --locked pytest -q
```

Use a unique date or suffix for the update branch. Inspect dependency changes
and validate lockfile consistency; retain TLS certificate verification and the
gateway's path restrictions. Tests use temporary files and local HTTP/HTTPS
fixtures, so the test runner needs loopback socket access.

Run read-only compatibility checks against the installed REST plugin for affected
read tools. Validate write changes with disposable notes in an allowed folder.
Unit tests alone do not establish compatibility with the real REST API. Review
the resulting changes in this fork before merging; deploy separately using the
[deployment runbook](../deployment.md). Configure PRs to target this fork rather
than the original project unless the change is intentionally being contributed.

## Migration record: 2026-09-27

The migration starts at upstream `5ee0b84fa8319fd2fdf0db0ee1febb065e712a15`.
The preserved local tip is `ff21e7dd33376034044ed53ae8f82a87d1966d04`.
The client, gateway, deployment, and documentation/packaging were transplanted
in four commits. Commit `6bc001d9e836ed9fd2138e954e1cd1e50c7890f4` then joins the
legacy history with an `ours`-strategy merge. That one-time merge preserves old
commit identities; regular upstream updates use normal merges.

The migration commit and the old local tip have the identical Git tree
`3410eb2e41cfde31876d0b8c15973a146732d991`: same paths, file contents, and modes.
Both upstream HEAD and the legacy tip are ancestors of the migration commit.
Subsequent migration documentation changes are limited to this file and README.

```sh
git diff --exit-code ff21e7d 6bc001d9
git merge-base --is-ancestor 5ee0b84 6bc001d9
git merge-base --is-ancestor ff21e7d 6bc001d9
git cat-file -e e7af3f9cfbbb1d559c179727cf060af9c92aad34^{commit}
```

The pre-migration suite passed all 307 tests. The migration does not activate a
release, change runtime settings, or fix the existing PATCH and periodic-note
compatibility gaps. Those are separate follow-up changes.

To inspect the previous source without changing the runtime or the main checkout:

```sh
git worktree add --detach /tmp/obsidian-pre-fork archive/pre-fork-20260927
```

Choose an unused worktree path. The complete pre-migration history was also
verified in a local Git bundle before the migration; the archive branch and
retained ancestry are the durable copies after publication.
