# Local TLS verification patch

Base: https://github.com/MarkusPfundstein/mcp-obsidian
Commit: 32285e9ac07049a8a23ea7d7903603a3e48a1bf7

Change: default `Obsidian.verify_ssl` to `True`. Requests can then use
`REQUESTS_CA_BUNDLE` to trust the locally exported Obsidian certificate.
The launcher pins MCP 1.29.0 and Requests 2.34.2, matching the inspected installation.

Regression coverage: a local HTTPS fixture rejects an untrusted certificate
and a mismatched hostname, and accepts the explicitly configured CA bundle.
The existing request test now expects verification to be enabled.

This copy must be maintained explicitly when updating upstream. It does not
implement write-folder restrictions or otherwise certify the MCP as secure.
The configured Obsidian certificate expires on 2027-09-20 12:34:26 UTC.
Replace the trusted certificate when renewing the Obsidian certificate.
