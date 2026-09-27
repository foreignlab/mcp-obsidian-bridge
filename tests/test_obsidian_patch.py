"""Regression coverage for the Local REST API 5.x legacy PATCH contract."""

from unittest.mock import patch

import pytest
import requests

from mcp_obsidian.obsidian import Obsidian


@pytest.mark.parametrize("operation", ["append", "prepend", "replace"])
@pytest.mark.parametrize(
    ("target_type", "target", "encoded_target"),
    [
        ("heading", "Outer::日本語", "Outer%3A%3A%E6%97%A5%E6%9C%AC%E8%AA%9E"),
        ("block", "block-id", "block-id"),
        ("frontmatter", "status", "status"),
    ],
)
def test_patch_explicitly_selects_legacy_format(operation, target_type, target, encoded_target):
    api = Obsidian(api_key="test-key", protocol="https", host="localhost", port=27124)
    response = requests.Response()
    response.status_code = 204

    with patch("mcp_obsidian.obsidian.requests.patch", return_value=response) as send:
        assert api.patch_content("note.md", operation, target_type, target, "追記 🚀") is None

    send.assert_called_once_with(
        "https://localhost:27124/vault/note.md",
        headers={
            "Authorization": "Bearer test-key",
            "Markdown-Patch-Version": "1",
            "Content-Type": "text/markdown",
            "Operation": operation,
            "Target-Type": target_type,
            "Target": encoded_target,
        },
        data="追記 🚀".encode("utf-8"),
        verify=True,
        timeout=(3, 6),
    )
