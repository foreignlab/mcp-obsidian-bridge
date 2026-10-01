#!/usr/bin/env python3
"""Read-only shared stdio probe; output contains counts, never Vault data."""

import argparse
import asyncio
from contextlib import contextmanager
from datetime import timedelta
import json
import logging
from pathlib import Path
import tempfile
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from probe_gateway import validate_recent
from shared_connection import ClientProfile, SharedRuntimeError, clean_environment, read_client_profile

EXPECTED_TOOLS = {
    'obsidian_list_files_in_vault', 'obsidian_list_files_in_dir', 'obsidian_get_file_contents',
    'obsidian_simple_search', 'obsidian_append_content', 'obsidian_patch_content',
    'obsidian_put_content', 'obsidian_delete_file', 'obsidian_complex_search',
    'obsidian_search_by_tag', 'obsidian_get_frontmatter', 'obsidian_batch_get_file_contents',
    'obsidian_get_periodic_note', 'obsidian_get_recent_periodic_notes', 'obsidian_get_recent_changes',
}


def validate_catalog(tools):
    names = [tool.name for tool in tools]
    if len(names) != len(EXPECTED_TOOLS) or set(names) != EXPECTED_TOOLS:
        raise SharedRuntimeError('Unexpected shared tool catalog')


def decode_rows(result):
    try:
        if result.isError or len(result.content) != 1:
            raise ValueError()
        rows = json.loads(result.content[0].text)
        if not isinstance(rows, list):
            raise ValueError()
        return rows
    except (ValueError, TypeError, AttributeError):
        raise SharedRuntimeError('Invalid shared tool result') from None


@contextmanager
def quiet_sdk():
    logger = logging.getLogger('mcp')
    old = logger.handlers, logger.propagate
    logger.handlers, logger.propagate = [logging.NullHandler()], False
    try:
        yield
    finally:
        logger.handlers, logger.propagate = old


async def probe(profile: ClientProfile, *, command: list[str], cwd: Path, managed: bool = False) -> dict:
    params = StdioServerParameters(command=command[0], args=command[1:],
                                   cwd=str(cwd), env=clean_environment(profile.env, managed=managed))
    with quiet_sdk(), tempfile.TemporaryFile(mode='w+') as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session:
                info = await session.initialize()
                if info.serverInfo.name != 'mcp-obsidian':
                    raise SharedRuntimeError('Unexpected shared server identity')
                tools = (await session.list_tools()).tools
                validate_catalog(tools)
                files = decode_rows(await session.call_tool('obsidian_list_files_in_vault', {}))
                if any(not isinstance(path, str) for path in files):
                    raise SharedRuntimeError('Invalid Vault listing')
                rows = decode_rows(await session.call_tool('obsidian_get_recent_changes', {'days': 1, 'limit': 3}))
                return {'ok': True, 'tools': len(tools), 'vault_entries': len(files),
                        'recent_changes': validate_recent(rows)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-config', type=Path, required=True)
    parser.add_argument('--server', required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--executable', type=Path)
    target.add_argument('--launcher', type=Path)
    target.add_argument('--launch-record', type=Path)
    parser.add_argument('--cwd', type=Path)
    args = parser.parse_args(argv)
    try:
        profile = read_client_profile(args.client_config, args.server)
        if args.executable:
            if not args.cwd:
                raise SharedRuntimeError('Executable probe requires a working directory')
            command, cwd = [str(args.executable)], args.cwd
        elif args.launcher:
            command, cwd = [str(args.launcher)], args.launcher.parent
        else:
            record = json.loads(args.launch_record.read_text())
            command, cwd = [record['command'], *record['args']], Path(record['cwd'])
        result = asyncio.run(asyncio.wait_for(probe(profile, command=command, cwd=cwd,
            managed=args.executable is not None), timeout=45))
        print(json.dumps(result))
    except Exception:
        print(json.dumps({'error': 'Shared probe failed; private details suppressed'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
