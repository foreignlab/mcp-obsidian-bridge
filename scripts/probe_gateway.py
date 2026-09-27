#!/usr/bin/env python3
"""Read-only stdio gateway probe; output contains counts, never Vault paths."""

import argparse
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_recent(rows):
    require(isinstance(rows, list) and len(rows) <= 3, 'Invalid Recent Changes count')
    cutoff = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)).timestamp()
    previous = float('inf')
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('filename'), str), 'Invalid Recent Changes row')
        try:
            stamp = datetime.fromisoformat(row['result']['file.mtime'])
        except (TypeError, ValueError, KeyError):
            raise ValueError('Invalid Recent Changes timestamp') from None
        require(stamp.tzinfo is not None, 'Recent Changes timestamp needs a timezone')
        epoch = stamp.timestamp()
        require(cutoff <= epoch <= previous, 'Recent Changes boundary/order mismatch')
        previous = epoch
    return len(rows)


async def probe(*, gateway=None, launcher=None, skip_recent=False):
    if launcher:
        params = StdioServerParameters(command=str(launcher), cwd=launcher.parent)
    else:
        params = StdioServerParameters(command=sys.executable, args=[str(gateway)], cwd=gateway.parent)
    # Child errors may contain legacy SDK messages. Never relay their text.
    with tempfile.TemporaryFile(mode='w+') as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session:
                initialized = await session.initialize()
                require(initialized.serverInfo.name == 'obsidian-chatgpt-restricted', 'Unexpected MCP server')
                catalog = (await session.list_tools()).tools
                names = {tool.name for tool in catalog}
                expected = {
                    'obsidian_list_files_in_vault', 'obsidian_list_files_in_dir',
                    'obsidian_get_file_contents', 'obsidian_batch_get_file_contents',
                    'obsidian_simple_search', 'obsidian_search_by_tag',
                    'obsidian_get_frontmatter', 'obsidian_get_recent_changes',
                    'obsidian_put_content', 'obsidian_append_content',
                    'obsidian_patch_content', 'obsidian_delete_file',
                }
                require(names == expected, 'Unexpected gateway tool catalog')
                listing = await session.call_tool('obsidian_list_files_in_vault', {})
                require(not listing.isError, 'Vault listing failed')
                files = json.loads(listing.content[0].text)
                require(isinstance(files, list), 'Unexpected Vault listing')
                summary = {'ok': True, 'tools': len(names), 'vault_entries': len(files)}
                if not skip_recent:
                    result = await session.call_tool('obsidian_get_recent_changes', {'limit': 3, 'days': 1})
                    require(not result.isError, 'Recent Changes failed')
                    summary['recent_changes'] = validate_recent(json.loads(result.content[0].text))
                return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--gateway', type=Path)
    target.add_argument('--launcher', type=Path)
    parser.add_argument('--skip-recent', action='store_true', help='Legacy rollback check only')
    args = parser.parse_args()
    try:
        result = asyncio.run(asyncio.wait_for(probe(**vars(args)), timeout=45))
        print(json.dumps(result))
    except Exception:
        print(json.dumps({'error': 'Gateway probe failed; details suppressed to protect Vault data'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
