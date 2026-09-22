import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import threading
from urllib.parse import unquote

import pytest

os.environ['OBSIDIAN_API_KEY'] = 'test-only-key'

from gateway import create_gateway
from mcp_obsidian import obsidian


@pytest.fixture
def service(tmp_path, monkeypatch):
    for folder in ('000_Inbox', '020_Projects', '040_Literature_Notes'):
        (tmp_path / folder).mkdir()
    original = tmp_path / '040_Literature_Notes/note.md'
    original.write_text('protected content')
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(('GET', self.path))
            self.send_response(200)
            self.end_headers()
            self.wfile.write((tmp_path / unquote(self.path.removeprefix('/vault/'))).read_bytes())

        def do_PUT(self):
            requests.append(('PUT', self.path))
            path = tmp_path / unquote(self.path.removeprefix('/vault/'))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_class = obsidian.Obsidian
    monkeypatch.setattr(obsidian, 'Obsidian', lambda **kwargs: original_class(
        api_key='test-only-key', protocol='http', host='127.0.0.1', port=server.server_port,
    ))
    monkeypatch.setenv('NO_PROXY', '*')
    app, list_tools, call_tool = create_gateway(tmp_path)
    try:
        yield tmp_path, list_tools, call_tool, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('folder', ['000_Inbox', '020_Projects'])
def test_tool_call_writes_allowed_file_through_rest(service, folder):
    vault, _, call, requests = service
    path = f'{folder}/日本語 #1?.md'
    asyncio.run(call('obsidian_put_content', {'filepath': path, 'content': 'new content'}))
    assert (vault / path).read_text() == 'new content'
    assert requests[0][1].endswith('/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%231%3F.md')


@pytest.mark.parametrize('tool', ['obsidian_put_content', 'obsidian_append_content', 'obsidian_patch_content', 'obsidian_delete_file'])
def test_tool_call_rejects_outside_write_before_rest(service, tool):
    vault, _, call, requests = service
    args = {'filepath': '040_Literature_Notes/note.md'}
    if tool == 'obsidian_delete_file':
        args['confirm'] = True
    else:
        args['content'] = 'forbidden'
    if tool == 'obsidian_patch_content':
        args.update(operation='replace', target_type='heading', target='test')
    with pytest.raises(PermissionError):
        asyncio.run(call(tool, args))
    assert requests == []
    assert (vault / '040_Literature_Notes/note.md').read_text() == 'protected content'


def test_tool_call_reads_outside_write_folders(service):
    _, _, call, _ = service
    result = asyncio.run(call('obsidian_get_file_contents', {'filepath': '040_Literature_Notes/note.md'}))
    assert 'protected content' in result[0].text


@pytest.mark.parametrize('args', [
    {'filepath': '000_Inbox/a.md', 'content': 42},
    {'filepath': '000_Inbox/a.md', 'content': 'test', 'extra': 'not allowed'},
])
def test_schema_validation_prevents_backend_call(service, args):
    _, _, call, requests = service
    with pytest.raises(ValueError):
        asyncio.run(call('obsidian_put_content', args))
    assert requests == []


def test_advertised_tools_match_enforced_policy(service):
    _, list_tools, call, requests = service
    catalog = {t.name: t for t in asyncio.run(list_tools())}
    assert len(catalog) == 12
    assert catalog['obsidian_get_file_contents'].annotations.readOnlyHint is True
    assert catalog['obsidian_put_content'].annotations.destructiveHint is True
    assert catalog['obsidian_put_content'].annotations.readOnlyHint is False
    assert 'obsidian_complex_search' not in catalog
    with pytest.raises(PermissionError):
        asyncio.run(call('obsidian_complex_search', {'query': {}}))
    assert requests == []
