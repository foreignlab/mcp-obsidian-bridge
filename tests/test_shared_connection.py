import importlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
connection = importlib.import_module('shared_connection')


@pytest.fixture
def profile_file(tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_text('synthetic certificate')
    env = {'OBSIDIAN_API_KEY': 'selected-sentinel-key', 'OBSIDIAN_HOST': 'localhost',
           'OBSIDIAN_PORT': '27124', 'OBSIDIAN_PROTOCOL': 'https', 'REQUESTS_CA_BUNDLE': str(ca)}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'mcpServers': {'obsidian': {'command': sys.executable,
                                                         'args': [], 'env': env}}}))
    return path


def test_named_profile_overrides_ambient_connection(profile_file, monkeypatch):
    monkeypatch.setenv('OBSIDIAN_API_KEY', 'wrong-ambient-key')
    monkeypatch.setenv('PYTHONPATH', '/wrong')
    monkeypatch.setenv('UV_PROJECT_ENVIRONMENT', '/wrong')
    profile = connection.read_client_profile(profile_file, 'obsidian')
    env = connection.clean_environment(profile.env)
    assert env['OBSIDIAN_API_KEY'] == 'selected-sentinel-key'
    assert 'selected-sentinel-key' not in repr(profile)
    assert 'PYTHONPATH' not in env and 'UV_PROJECT_ENVIRONMENT' not in env
    assert profile.cwd is None


def test_toml_selects_only_named_entry(profile_file, tmp_path):
    env = json.loads(profile_file.read_text())['mcpServers']['obsidian']['env']
    path = tmp_path / 'config.toml'
    lines = ['[mcp_servers.obsidian]', f'command = {json.dumps(sys.executable)}',
             'args = ["one"]', f'cwd = {json.dumps(str(tmp_path))}', '[mcp_servers.obsidian.env]']
    lines += [f'{key} = {json.dumps(value)}' for key, value in env.items()]
    lines += ['[mcp_servers.unrelated]', 'command = 42']
    path.write_text('\n'.join(lines))
    profile = connection.read_client_profile(path, 'obsidian')
    assert profile.args == ['one'] and profile.cwd == tmp_path


@pytest.mark.parametrize('change', [
    ('command', 42), ('command', ''), ('args', 'wrong'), ('args', [42]),
    ('env', []), ('cwd', 42), ('env.OBSIDIAN_PROTOCOL', 'http'),
    ('env.OBSIDIAN_API_KEY', ''), ('env.OBSIDIAN_PORT', '70000'),
    ('env.OBSIDIAN_HOST', ''), ('env.REQUESTS_CA_BUNDLE', '/missing-sentinel'),
    ('env.OBSIDIAN_PORT', 27124),
])
def test_bad_profiles_have_fixed_private_errors(profile_file, change):
    data = json.loads(profile_file.read_text())
    row = data['mcpServers']['obsidian']
    key, value = change
    if key.startswith('env.'):
        row['env'][key[4:]] = value
    else:
        row[key] = value
    profile_file.write_text(json.dumps(data))
    with pytest.raises(connection.SharedRuntimeError) as error:
        connection.read_client_profile(profile_file, 'obsidian')
    assert 'selected-sentinel-key' not in str(error.value)
    assert 'missing-sentinel' not in str(error.value)


def test_missing_server_and_invalid_document_are_private(profile_file):
    with pytest.raises(connection.SharedRuntimeError):
        connection.read_client_profile(profile_file, 'missing')
    profile_file.write_text('selected-sentinel-key')
    with pytest.raises(connection.SharedRuntimeError) as error:
        connection.read_client_profile(profile_file, 'obsidian')
    assert 'selected-sentinel-key' not in str(error.value)


def test_reserved_profile_keys_cannot_redirect_interpreter(profile_file):
    profile = connection.read_client_profile(profile_file, 'obsidian')
    profile.env.update(PYTHONPATH='/wrong', UV_INDEX='wrong', VIRTUAL_ENV='/wrong')
    env = connection.clean_environment(profile.env)
    assert not {'PYTHONPATH', 'UV_INDEX', 'VIRTUAL_ENV'} & env.keys()

