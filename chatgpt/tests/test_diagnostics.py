import asyncio
from contextlib import asynccontextmanager
import io
import json
import logging
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import pytest
import requests
from mcp.types import CallToolRequest, CallToolRequestParams

import gateway

os.environ.setdefault('OBSIDIAN_API_KEY', 'test-only-key')

from mcp_obsidian import obsidian


SECRET = 'private-note-and-key-DO-NOT-LOG'


def records(capsys):
    output = capsys.readouterr()
    assert output.out == ''
    assert SECRET not in output.err
    return [json.loads(line) for line in output.err.splitlines()]


def api_response(status=200, payload=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    return response


def test_tool_records_correlated_start_and_completion_without_contents(tmp_path, capsys):
    _, _, call = gateway.create_gateway(tmp_path)
    with patch('mcp_obsidian.obsidian.requests.get', return_value=api_response(payload={'files': [SECRET]})):
        result = asyncio.run(call('obsidian_list_files_in_vault', {}))
    assert SECRET in result[0].text
    logs = records(capsys)
    assert [r['event'] for r in logs] == ['tool_started', 'tool_completed']
    assert logs[0]['call_id'] == logs[1]['call_id']
    assert logs[0]['run_id'] == logs[1]['run_id']
    assert logs[1]['outcome'] == 'success'
    assert logs[1]['duration_ms'] >= 0
    assert logs[0]['timestamp'].endswith('+00:00')


@pytest.mark.parametrize(('name', 'arguments', 'category'), [
    (SECRET, {}, 'tool_not_allowed'),
    ('obsidian_get_file_contents', {'filepath': SECRET, 'extra': SECRET}, 'invalid_arguments'),
    ('obsidian_put_content', {'filepath': f'040_Literature_Notes/{SECRET}.md', 'content': SECRET}, 'policy_rejected'),
])
def test_sdk_dispatch_logs_rejected_calls_without_input(tmp_path, capsys, name, arguments, category):
    app, _, _ = gateway.create_gateway(tmp_path)
    request = CallToolRequest(params=CallToolRequestParams(name=name, arguments=arguments))
    result = asyncio.run(app.request_handlers[CallToolRequest](request))
    assert result.root.isError is True
    logs = records(capsys)
    assert logs[-1]['event'] == 'tool_failed'
    assert logs[-1]['category'] == category
    assert logs[-1]['tool'] == ('unknown' if name == SECRET else name)


@pytest.mark.parametrize(('status', 'code', 'category'), [
    (401, 40101, 'api_auth'),
    (403, 40300, 'api_auth'),
    (400, 40012, 'api_http'),
    (500, SECRET, 'api_http'),
])
def test_api_failure_logs_only_numeric_status_and_code(tmp_path, capsys, status, code, category):
    _, _, call = gateway.create_gateway(tmp_path)
    response = api_response(status, {'errorCode': code, 'message': SECRET})
    with patch('mcp_obsidian.obsidian.requests.get', return_value=response):
        with pytest.raises(Exception):
            asyncio.run(call('obsidian_get_file_contents', {'filepath': SECRET + '.md'}))
    logs = records(capsys)
    assert [r['event'] for r in logs] == ['tool_started', 'api_error', 'tool_failed']
    assert logs[1]['http_status'] == status
    assert logs[1]['category'] == logs[2]['category'] == category
    if isinstance(code, int):
        assert logs[1]['api_error_code'] == code
    else:
        assert 'api_error_code' not in logs[1]


@pytest.mark.parametrize(('error', 'category'), [
    (requests.Timeout(SECRET), 'api_timeout'),
    (requests.exceptions.SSLError(SECRET), 'api_tls'),
    (requests.ConnectionError(SECRET), 'api_connection'),
])
def test_transport_failures_are_classified_without_exception_text(tmp_path, capsys, error, category):
    _, _, call = gateway.create_gateway(tmp_path)
    with patch('mcp_obsidian.obsidian.requests.get', side_effect=error):
        with pytest.raises(Exception):
            asyncio.run(call('obsidian_get_file_contents', {'filepath': SECRET + '.md'}))
    logs = records(capsys)
    assert logs[-1]['category'] == category
    assert logs[1]['category'] == category


def test_batch_partial_failure_is_not_logged_as_success(tmp_path, capsys):
    _, _, call = gateway.create_gateway(tmp_path)
    with patch('mcp_obsidian.obsidian.requests.get', side_effect=requests.Timeout(SECRET)):
        asyncio.run(call('obsidian_batch_get_file_contents', {'filepaths': [SECRET + '.md']}))
    logs = records(capsys)
    assert logs[-1]['event'] == 'tool_completed'
    assert logs[-1]['outcome'] == 'completed_with_api_errors'
    assert logs[-1]['api_error_count'] == 1


def test_call_ids_are_distinct_and_error_context_is_reset(tmp_path, capsys):
    _, _, call = gateway.create_gateway(tmp_path)
    with patch('mcp_obsidian.obsidian.requests.get', side_effect=[
        requests.Timeout(SECRET), api_response(payload={'files': []}),
    ]):
        with pytest.raises(Exception):
            asyncio.run(call('obsidian_list_files_in_vault', {}))
        asyncio.run(call('obsidian_list_files_in_vault', {}))
    logs = records(capsys)
    assert logs[0]['call_id'] != logs[-1]['call_id']
    assert logs[-1]['outcome'] == 'success'
    assert logs[-1]['api_error_count'] == 0


def test_logging_failure_does_not_turn_completed_write_into_error(tmp_path):
    class BrokenStream(io.StringIO):
        def write(self, value):
            raise OSError('full disk')

    (tmp_path / '000_Inbox').mkdir()
    _, _, call = gateway.create_gateway(tmp_path)
    with patch('sys.stderr', BrokenStream()), patch(
        'mcp_obsidian.obsidian.requests.put', return_value=api_response(status=204),
    ) as put:
        asyncio.run(call('obsidian_put_content', {'filepath': '000_Inbox/test.md', 'content': SECRET}))
    assert put.call_count == 1


def test_startup_failure_has_stage_and_no_traceback(monkeypatch, capsys):
    monkeypatch.setattr(gateway.Path, 'read_text', lambda self: '{' + SECRET)
    with pytest.raises(SystemExit) as error:
        asyncio.run(gateway.main())
    assert error.value.code == 1
    logs = records(capsys)
    assert [r['event'] for r in logs] == ['gateway_starting', 'gateway_failed']
    assert logs[-1]['stage'] == 'load_config'


@pytest.mark.parametrize('stage', ['https_config', 'ca_config', 'vault_config', 'api_key_check'])
def test_startup_validation_failure_identifies_stage(tmp_path, monkeypatch, capsys, stage):
    plugin = tmp_path / '.obsidian/plugins/obsidian-local-rest-api'
    plugin.mkdir(parents=True)
    if stage != 'vault_config':
        (plugin / 'data.json').write_text(json.dumps({
            'apiKey': 'different-key' if stage == 'api_key_check' else SECRET,
        }))
    ca = tmp_path / (SECRET + '.pem')
    if stage != 'ca_config':
        ca.touch()
    (tmp_path / 'connection.json').write_text(json.dumps({
        'vault': str(tmp_path), 'env': {
            'OBSIDIAN_PROTOCOL': 'http' if stage == 'https_config' else 'https',
            'REQUESTS_CA_BUNDLE': str(ca), 'OBSIDIAN_API_KEY': SECRET,
        },
    }))
    monkeypatch.setattr(gateway, '__file__', str(tmp_path / 'gateway.py'))
    with pytest.raises(SystemExit) as error:
        asyncio.run(gateway.main())
    assert error.value.code == 1
    logs = records(capsys)
    assert logs[-1]['stage'] == stage
    assert 'gateway_ready' not in [r['event'] for r in logs]


def test_main_logs_lifecycle_and_redacts_library_warnings(tmp_path, monkeypatch, capsys):
    ca = tmp_path / 'test-ca.pem'
    ca.touch()
    config = {'vault': str(tmp_path), 'env': {
        'OBSIDIAN_PROTOCOL': 'https', 'REQUESTS_CA_BUNDLE': str(ca),
        'OBSIDIAN_API_KEY': SECRET,
    }}
    monkeypatch.setattr(gateway.Path, 'read_text', lambda self: json.dumps(
        config if self.name == 'connection.json' else {'apiKey': SECRET},
    ))
    monkeypatch.setenv('OBSIDIAN_API_KEY', 'restore-after-test')
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', 'restore-after-test')
    monkeypatch.setenv('OBSIDIAN_PROTOCOL', 'https')

    @asynccontextmanager
    async def stdio():
        yield None, None

    class FakeServer:
        def create_initialization_options(self):
            return None

        async def run(self, *args):
            logging.getLogger('mcp.server.lowlevel.server').warning('Unknown tool %s', SECRET)
            try:
                raise ValueError(SECRET)
            except ValueError:
                logging.getLogger('mcp.server.lowlevel.server').exception('Unexpected error')

    monkeypatch.setattr(gateway, 'stdio_server', stdio)
    monkeypatch.setattr(gateway, 'create_gateway', lambda vault, **kwargs: (FakeServer(), None, None))
    asyncio.run(gateway.main())
    logs = records(capsys)
    assert [r['event'] for r in logs] == [
        'gateway_starting', 'gateway_ready', 'library_warning', 'library_error', 'gateway_stopped',
    ]
    assert len({r['run_id'] for r in logs}) == 1


def test_shared_client_without_gateway_has_no_diagnostic_output(capsys):
    with patch('mcp_obsidian.obsidian.requests.get', side_effect=requests.Timeout(SECRET)):
        with pytest.raises(Exception):
            obsidian.Obsidian('test-key').list_files_in_vault()
    assert records(capsys) == []


def test_stdio_handshake_and_rejections_keep_protocol_and_logs_separate(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    for name in ('gateway.py', 'diagnostics.py', 'log_store.py', 'policy.py'):
        shutil.copyfile(Path(gateway.__file__).with_name(name), tmp_path / name)
    plugin = tmp_path / '.obsidian/plugins/obsidian-local-rest-api'
    plugin.mkdir(parents=True)
    (plugin / 'data.json').write_text(json.dumps({'apiKey': SECRET}))
    ca = tmp_path / 'fake-ca.pem'
    ca.touch()
    (tmp_path / 'connection.json').write_text(json.dumps({
        'vault': str(tmp_path), 'env': {
            'OBSIDIAN_PROTOCOL': 'https', 'REQUESTS_CA_BUNDLE': str(ca),
            'OBSIDIAN_API_KEY': SECRET,
        },
    }))

    async def exercise(log):
        params = StdioServerParameters(
            command=sys.executable, args=[str(tmp_path / 'gateway.py')],
            env={'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')},
            cwd=tmp_path,
        )
        async with stdio_client(params, errlog=log) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == 'obsidian-chatgpt-restricted'
                result = await session.call_tool(SECRET, {})
                assert result.isError is True
                result = await session.call_tool('obsidian_get_file_contents', {'filepath': SECRET, 'extra': SECRET})
                assert result.isError is True
                assert SECRET not in result.content[0].text
                tools = await session.list_tools()
                assert len(tools.tools) == 12

    with (tmp_path / 'stderr.log').open('w+') as log:
        asyncio.run(asyncio.wait_for(exercise(log), timeout=10))
        log.seek(0)
        contents = log.read()
    assert SECRET not in contents
    logs = [json.loads(line) for line in contents.splitlines()]
    events = [r['event'] for r in logs]
    assert events[:2] == ['gateway_starting', 'gateway_ready']
    assert 'mcp_initialized' in events
    assert events[-1] == 'gateway_stopped'
    assert len({r['run_id'] for r in logs}) == 1
    failures = [r for r in logs if r['event'] == 'tool_failed']
    assert [r['category'] for r in failures] == ['tool_not_allowed', 'invalid_arguments']
