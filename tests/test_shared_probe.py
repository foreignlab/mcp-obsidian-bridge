import asyncio
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
probe = importlib.import_module('probe_shared')
from tests.test_shared_connection import profile_file

TOOLS = {
    'obsidian_list_files_in_vault', 'obsidian_list_files_in_dir', 'obsidian_get_file_contents',
    'obsidian_simple_search', 'obsidian_append_content', 'obsidian_patch_content',
    'obsidian_put_content', 'obsidian_delete_file', 'obsidian_complex_search',
    'obsidian_search_by_tag', 'obsidian_get_frontmatter', 'obsidian_batch_get_file_contents',
    'obsidian_get_periodic_note', 'obsidian_get_recent_periodic_notes', 'obsidian_get_recent_changes',
}


@pytest.mark.parametrize('managed', [False, True])
def test_real_stdio_probe_is_read_only_and_hides_child_stderr(profile_file, tmp_path, capsys, managed):
    server = tmp_path / 'synthetic.py'
    server.write_text('import sys\nassert sys.pycache_prefix == ' + repr('/dev/null' if managed else None)
        + '\nassert sys.dont_write_bytecode == ' + repr(managed) + '\n' + '''import json, sys
print("selected-sentinel-key private-note-content", file=sys.stderr, flush=True)
tools = ''' + repr(sorted(TOOLS)) + '''
for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg: continue
    method = msg['method']
    if method == 'initialize':
        result = {'protocolVersion': msg['params']['protocolVersion'], 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'mcp-obsidian', 'version': 'test'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': t, 'inputSchema': {'type': 'object'}} for t in tools]}
    elif method == 'tools/call':
        assert msg['params']['name'] in ('obsidian_list_files_in_vault', 'obsidian_get_recent_changes')
        result = {'content': [{'type': 'text', 'text': '[]'}], 'isError': False}
    else: raise RuntimeError('unexpected method')
    print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}), flush=True)
''')
    profile = probe.read_client_profile(profile_file, 'obsidian')
    profile.env['PYTHONPYCACHEPREFIX'] = '/wrong-cache'
    result = asyncio.run(probe.probe(profile, command=[sys.executable, str(server)], cwd=tmp_path, managed=managed))
    assert result == {'ok': True, 'tools': 15, 'vault_entries': 0, 'recent_changes': 0}
    assert 'sentinel' not in capsys.readouterr().err


@pytest.mark.parametrize('names', [list(TOOLS)[:-1], list(TOOLS) + ['extra'], list(TOOLS) + [next(iter(TOOLS))]])
def test_catalog_requires_exact_names_without_duplicates(names):
    with pytest.raises(probe.SharedRuntimeError):
        probe.validate_catalog([SimpleNamespace(name=name) for name in names])


@pytest.mark.parametrize('result', [
    SimpleNamespace(isError=True, content=[]), SimpleNamespace(isError=False, content=[]),
    SimpleNamespace(isError=False, content=[SimpleNamespace(text='private-note-content')]),
    SimpleNamespace(isError=False, content=[SimpleNamespace(text='{}')]),
])
def test_invalid_tool_results_are_rejected_privately(result):
    with pytest.raises(probe.SharedRuntimeError) as error:
        probe.decode_rows(result)
    assert 'private-note-content' not in str(error.value)


def test_recent_metadata_boundary_validation():
    rows = [{'filename': 'synthetic.md', 'result': {'file.mtime': datetime.now(timezone.utc).isoformat()}}]
    assert probe.validate_recent(rows) == 1
    with pytest.raises(ValueError):
        probe.validate_recent([{'filename': 'synthetic.md', 'result': {'file.mtime': '2020-01-01T00:00:00+00:00'}}])


@pytest.mark.parametrize('failure', [TimeoutError('selected-sentinel-key'),
    ExceptionGroup('private-note-content', [ValueError('selected-sentinel-key')])])
def test_cli_masks_nested_failures(profile_file, tmp_path, monkeypatch, capsys, failure):
    async def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(probe, 'probe', fail)
    rc = probe.main(['--client-config', str(profile_file), '--server', 'obsidian',
                     '--executable', sys.executable, '--cwd', str(tmp_path)])
    assert rc == 1
    output = capsys.readouterr()
    assert 'sentinel' not in output.err and 'private-note' not in output.err
    assert 'error' in output.err
