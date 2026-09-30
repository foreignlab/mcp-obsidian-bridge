# Source releases

Source releases mark reviewed snapshots of this personal-use fork. Each release
has a Git tag, an English GitHub release note, and GitHub's source archives.
They help select a known source revision and understand its changes and verified
compatibility. The project's personal-use scope and support policy remain those
in the [README](../README.md#development).

## Names and timing

Use `bridge-YYYY.MM.DD`, with the date of publication in Asia/Tokyo. The first
release on a date has no suffix; further releases use `.2`, `.3`, and so on.
For example, `bridge-2026.09.30` followed by `bridge-2026.09.30.2`.
This date identifies a published source snapshot; compatibility and breaking
changes are described in the release note.

Create a release when reviewed fixes or features form a useful deployment
candidate, or when establishing an initial baseline. A documentation-only change
can join the next release; each merged PR does not need its own release.
Published tags stay at their original commits. Corrections to released source
require a new release. Release-note corrections may clarify the existing record
without changing its tag or source.

These names identify fork source releases separately from the Python package
metadata in `pyproject.toml`. Creating a date tag does not change the package
version or publish a package to a registry.

## Publication checklist

1. Choose the exact full commit SHA on this fork's `main`. Its changes must have
   completed the [development and review workflow](development.md#review-pr-and-merge).
2. Check that the main push for that commit completed all three test jobs
   successfully: Ubuntu Python 3.11 and 3.13, and macOS Python 3.13. PR checks
   alone do not establish the result of the merged tree.
3. Choose an unused date tag and prepare the release note below. Separate test
   results from live compatibility checks, naming the source revision and client
   path actually exercised. Mark missing live checks as unverified.
4. With authorization to publish, create an annotated tag at the chosen full SHA,
   push that tag to this fork, and publish the GitHub Release for that tag.
   Verify that the published tag resolves to the chosen SHA and the release
   contains the intended note. Refer to the specific tag when selecting source.

Publication uses the reviewed source and GitHub's generated source archives.
Runtime configuration, credentials, certificates, logs, and Vault files are
private installation data and must stay out of release notes and assets.

## Release-note contents

Use a short English note containing:

- **Source:** the tag and full source commit SHA.
- **Changes:** user-visible fixes and features since the previous source release,
  with links to this fork's relevant PRs. For the first release, summarize the
  baseline behavior and link to [fork maintenance](upstream/maintenance.md) for
  upstream ancestry and retained history.
- **Verification:** the main CI result, tested Local REST API versions, and live
  checks for each client path, including the revision exercised.
- **Known limitations:** affected tools, compatibility gaps, and any required
  operator action. Describe breaking changes explicitly.
- **Deployment:** the recorded deployment state as of publication, with links
  to any public deployment records. Later deployments have their own records.

Use synthetic examples when explaining failures. Keep note contents, private
paths, and connection details out of the public note.

## Source releases and installed runtimes

A source release and a runtime deployment are separate events. The ChatGPT
gateway and the shared stdio client are separate installations; they can run
different source revisions. Publishing a release does not update either one.

For each authorized deployment, record the target client, full source commit,
deployment date, verification performed, and previous revision or backup used
for rollback. Include the source tag when one exists. Keep private runtime
metadata locally; a public record under `docs/deployments/` contains only the
sanitized outcome and verification limits.

Use the [ChatGPT deployment runbook](deployment.md) for its preparation,
activation, and rollback. That runtime continues to use full commit SHAs as
release directory names. Documentation-only source releases can leave both
installed runtimes at earlier revisions; record their actual revisions instead
of inferring them from the newest tag. Shared-client deployment is a separate
procedure from this ChatGPT runbook.
