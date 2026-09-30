import hashlib
import importlib
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
deployment = importlib.import_module('deploy_shared')
from tests.test_shared_connection import profile_file


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()


@pytest.fixture
def shared_runtime(tmp_path, profile_file, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    (repo / 'src/mcp_obsidian').mkdir(parents=True)
    (repo / 'src/mcp_obsidian/__init__.py').write_text('VERSION = 1\n')
    (repo / 'pyproject.toml').write_text('[project]\nname="mcp-obsidian"\nversion="0.2.2"\n')
    (repo / 'uv.lock').write_text('synthetic lock\n')
    (repo / 'scripts').mkdir()
    for name in ['probe_shared.py', 'shared_connection.py', 'probe_gateway.py']:
        (repo / 'scripts' / name).write_text('pass\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'first')
    root = tmp_path / 'runtime with spaces'
    root.mkdir()
    shutil.copytree(repo / 'src', root / 'src')
    shutil.copy(repo / 'pyproject.toml', root / 'pyproject.toml')
    shutil.copy(repo / 'uv.lock', root / 'uv.lock')
    legacy = root / 'legacy-command'
    legacy.write_text('#!/bin/sh\nprintf "legacy\\n"\n')
    legacy.chmod(0o700)
    data = json.loads(profile_file.read_text())
    data['mcpServers']['obsidian']['command'] = str(legacy)
    profile_file.write_text(json.dumps(data))
    app = deployment.SharedDeployment(root, repo, client_config=profile_file,
             server='obsidian', legacy_cwd=root)

    def build(release):
        site = release / '.venv/site/mcp_obsidian'
        site.parent.mkdir(parents=True, exist_ok=True)
        if site.exists():
            shutil.rmtree(site)
        shutil.copytree(release / 'src/mcp_obsidian', site)
        binary = release / '.venv/bin'
        binary.mkdir(parents=True, exist_ok=True)
        python = binary / 'python'
        python.write_text('#!' + sys.executable + '\nimport hashlib,json,pathlib\n'
            + 'root=pathlib.Path(__file__).parents[1]/"site"\n'
            + 'print(json.dumps({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() '
              'for p in (root/"mcp_obsidian").rglob("*.py")}))\n')
        python.chmod(0o700)
        entry = binary / 'mcp-obsidian'
        entry.write_text('#!/bin/sh\nprintf "managed\\n"\n')
        entry.chmod(0o700)
    monkeypatch.setattr(app, '_build', build)
    monkeypatch.setattr(app, '_probe', lambda *args, **kwargs:
        {'ok': True, 'tools': 15, 'vault_entries': 0, 'recent_changes': 0})
    return app


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in (root / 'src').rglob('*') if p.is_file()}


def test_prepare_is_idempotent_and_preserves_legacy(shared_runtime):
    app = shared_runtime
    original = snapshot(app.root)
    revision = app.prepare('HEAD')
    assert app.status()['selected'] == 'legacy'
    assert not (app.root / 'current').exists() and not (app.root / 'launch.sh').exists()
    assert snapshot(app.root) == original
    assert app.prepare(revision) == revision
    release, manifest = app._manifest(revision)
    assert manifest['status'] == 'prepared'
    assert 'selected-sentinel-key' not in (release / 'release.json').read_text()
    assert (app.control / 'legacy.json').stat().st_mode & 0o777 == 0o600


def test_date_tag_resolves_to_full_sha(shared_runtime):
    app = shared_runtime
    git(app.repo, 'tag', 'bridge-2026.09.30')
    assert app.prepare('bridge-2026.09.30') == git(app.repo, 'rev-parse', 'HEAD')


def test_dirty_source_rejected(shared_runtime):
    app = shared_runtime
    (app.repo / 'untracked').write_text('dirty')
    with pytest.raises(deployment.SharedRuntimeError, match='clean'):
        app.prepare('HEAD')
    assert app.status()['selected'] == 'legacy'


def test_explicit_legacy_cwd_required(shared_runtime):
    app = shared_runtime
    app.legacy_cwd = None
    with pytest.raises(deployment.SharedRuntimeError, match='working directory'):
        app.prepare('HEAD')


@pytest.mark.parametrize('where', ['source', 'installed', 'extra-installed', 'missing-installed', 'extra-source'])
def test_tampering_detected_even_when_archived_source_unchanged(shared_runtime, where):
    app = shared_runtime
    revision = app.prepare('HEAD')
    release = app.root / 'releases' / revision
    if where == 'source':
        (release / 'src/mcp_obsidian/__init__.py').write_text('changed')
    elif where == 'installed':
        (release / '.venv/site/mcp_obsidian/__init__.py').write_text('stale')
    elif where == 'missing-installed':
        (release / '.venv/site/mcp_obsidian/__init__.py').unlink()
    elif where == 'extra-installed':
        (release / '.venv/site/mcp_obsidian/extra.py').write_text('extra')
    else:
        (release / 'src/mcp_obsidian/extra.py').write_text('extra')
    with pytest.raises(deployment.SharedRuntimeError, match='integrity'):
        app._manifest(revision)


def test_failed_prepare_can_resume(shared_runtime, monkeypatch):
    app = shared_runtime
    with monkeypatch.context() as patch:
        patch.setattr(app, '_probe', lambda *args, **kwargs: (_ for _ in ()).throw(deployment.SharedRuntimeError('probe failed')))
        with pytest.raises(deployment.SharedRuntimeError):
            app.prepare('HEAD')
    revision = git(app.repo, 'rev-parse', 'HEAD')
    with pytest.raises(deployment.SharedRuntimeError, match='prepared'):
        app._manifest(revision)
    assert app.prepare('HEAD') == revision


def test_capture_legacy_only_once_after_client_adoption(shared_runtime):
    app = shared_runtime
    app.prepare('HEAD')
    old = (app.control / 'legacy.json').read_bytes()
    data = json.loads(app.client_config.read_text())
    data['mcpServers']['obsidian']['command'] = str(app.root / 'launch.sh')
    data['mcpServers']['obsidian']['env']['OBSIDIAN_API_KEY'] = 'new-secret'
    app.client_config.write_text(json.dumps(data))
    app.prepare('HEAD')
    assert (app.control / 'legacy.json').read_bytes() == old


@pytest.mark.parametrize('name,kind', [('../escape','file'),('/absolute','file'),('.env','file'),
    ('chatgpt/connection.json','file'),('bad','symlink'),('.venv/python','file')])
def test_unsafe_archive_members_rejected(tmp_path, name, kind):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        item = tarfile.TarInfo(name)
        if kind == 'symlink':
            item.type = tarfile.SYMTYPE
            item.linkname = '../escape'
            archive.addfile(item)
        else:
            item.size = 1
            archive.addfile(item, io.BytesIO(b'x'))
    with pytest.raises(deployment.SharedRuntimeError):
        deployment.extract_source(data.getvalue(), tmp_path / 'target')


def test_interrupted_extraction_is_retryable(shared_runtime, monkeypatch):
    app = shared_runtime
    original = deployment.extract_source
    def interrupt(data, target):
        original(data, target)
        raise KeyboardInterrupt()
    with monkeypatch.context() as patch:
        patch.setattr(deployment, 'extract_source', interrupt)
        with pytest.raises(KeyboardInterrupt):
            app.prepare('HEAD')
    assert app.prepare('HEAD') == git(app.repo, 'rev-parse', 'HEAD')


def test_build_has_locked_flags_and_clean_environment(shared_runtime, monkeypatch):
    app = shared_runtime
    monkeypatch.setenv('UV_PROJECT_ENVIRONMENT', 'wrong')
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b'', b'')
    monkeypatch.setattr(deployment.subprocess, 'run', run)
    deployment.SharedDeployment._build(app, app.root / 'releases/fake')
    command, kwargs = calls[0]
    assert all(flag in command for flag in ['--locked','--offline','--no-dev','--no-editable'])
    assert 'UV_PROJECT_ENVIRONMENT' not in kwargs['env']


def test_launcher_handles_spaces_and_protocol_stdout(shared_runtime):
    app = shared_runtime
    revision = app.prepare('HEAD')
    (app.root / 'current').symlink_to(app.root / 'releases' / revision)
    launch = app.root / 'launch.sh'
    launch.write_bytes(app._launcher())
    launch.chmod(0o700)
    assert subprocess.check_output([str(launch)]) == b'managed\n'
    (app.root / 'current').unlink()
    assert subprocess.check_output([str(launch)]) == b'legacy\n'


def test_real_distribution_metadata_reads_installed_code(shared_runtime):
    import venv
    app = shared_runtime
    revision = app.prepare('HEAD')
    release = app.root / 'releases' / revision
    venv.create(release / '.venv', clear=True, with_pip=False, symlinks=True)
    python = release / '.venv/bin/python'
    site = Path(subprocess.check_output([str(python), '-I', '-c',
        'import sysconfig; print(sysconfig.get_paths()["purelib"])']).decode().strip())
    shutil.copytree(release / 'src/mcp_obsidian', site / 'mcp_obsidian')
    metadata = site / 'mcp_obsidian-0.2.2.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text('Name: mcp-obsidian\nVersion: 0.2.2\n')
    expected = hashlib.sha256((release / 'src/mcp_obsidian/__init__.py').read_bytes()).hexdigest()
    assert app._installed_inventory(release) == {'mcp_obsidian/__init__.py': expected}
    (site / 'mcp_obsidian/extra.py').write_text('extra')
    with pytest.raises(deployment.SharedRuntimeError, match='integrity'):
        app._manifest(revision)


def test_first_activation_and_legacy_rollback_keep_stable_launcher(shared_runtime):
    app = shared_runtime
    revision = app.prepare('HEAD')
    app.activate(revision)
    assert app.status()['selected'] == revision and app.status()['previous'] == 'legacy'
    assert subprocess.check_output([str(app.root / 'launch.sh')]) == b'managed\n'
    app.rollback()
    assert app.status()['selected'] == 'legacy' and app.status()['pending'] is False
    assert subprocess.check_output([str(app.root / 'launch.sh')]) == b'legacy\n'
    app.rollback()
    assert app.status()['selected'] == revision


def second_release(app):
    (app.repo / 'src/mcp_obsidian/__init__.py').write_text('VERSION = 2\n')
    git(app.repo, 'commit', '-qam', 'second')
    return app.prepare('HEAD')


def test_managed_rollback_selects_retained_environment(shared_runtime):
    app = shared_runtime
    first = app.prepare('HEAD')
    app.activate(first)
    second = second_release(app)
    app.activate(second)
    app.rollback()
    assert app.status()['selected'] == first and app.status()['previous'] == second
    assert (app.root / 'current').resolve() == app.root / 'releases' / first


def test_failed_first_activation_restores_original_absence(shared_runtime, monkeypatch):
    app = shared_runtime
    revision = app.prepare('HEAD')
    old_probe = app._probe
    def fail(*args, **kwargs):
        if kwargs.get('launcher'):
            raise deployment.SharedRuntimeError('postflight failed')
        return old_probe(*args, **kwargs)
    monkeypatch.setattr(app, '_probe', fail)
    with pytest.raises(deployment.SharedRuntimeError, match='restored'):
        app.activate(revision)
    assert app.status()['selected'] == 'legacy' and not app.status()['pending']
    assert not (app.root / 'launch.sh').exists() and not (app.root / 'current').exists()


def test_failed_recovery_keeps_journal_and_can_retry(shared_runtime, monkeypatch):
    app = shared_runtime
    revision = app.prepare('HEAD')
    old_probe = app._probe
    def fail(target, **kwargs):
        if kwargs.get('launcher') or (target == 'legacy' and (app.control / 'pending.json').exists()):
            raise deployment.SharedRuntimeError('probe failed')
        return old_probe(target, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(app, '_probe', fail)
        with pytest.raises(deployment.SharedRuntimeError, match='recover'):
            app.activate(revision)
        assert app.status()['pending']
        saved = (app.control / 'pending.json').read_bytes()
        with pytest.raises(deployment.SharedRuntimeError):
            app.recover()
        assert (app.control / 'pending.json').read_bytes() == saved
    app.recover()
    assert not app.status()['pending'] and app.status()['selected'] == 'legacy'


@pytest.mark.parametrize('boundary', ['pointer', 'state', 'history', 'clear'])
def test_interruption_at_each_commit_boundary_is_recoverable(shared_runtime, monkeypatch, boundary):
    app = shared_runtime
    revision = app.prepare('HEAD')
    with monkeypatch.context() as patch:
        if boundary == 'pointer':
            original = app._pointer
            def interrupt(target):
                original(target)
                raise KeyboardInterrupt()
            patch.setattr(app, '_pointer', interrupt)
        elif boundary == 'state':
            original = deployment.write_json
            def interrupt(path, data):
                original(path, data)
                if path.name == 'state.json':
                    raise KeyboardInterrupt()
            patch.setattr(deployment, 'write_json', interrupt)
        else:
            def interrupt(*args, **kwargs):
                raise KeyboardInterrupt()
            patch.setattr(app, '_history' if boundary == 'history' else '_clear_pending', interrupt)
        with pytest.raises(KeyboardInterrupt):
            app.activate(revision)
        assert app.status()['pending']
    app.recover()
    assert app.status()['selected'] == 'legacy' and not app.status()['pending']
    assert not (app.root / 'launch.sh').exists()


def test_concurrency_and_pending_block_all_other_mutations(shared_runtime):
    app = shared_runtime
    with app._lock():
        with pytest.raises(deployment.SharedRuntimeError, match='Another'):
            app.prepare('HEAD')
    revision = app.prepare('HEAD')
    deployment.write_json(app.control / 'pending.json', {'synthetic': True})
    for operation in [lambda: app.prepare('HEAD'), lambda: app.activate(revision), app.rollback]:
        with pytest.raises(deployment.SharedRuntimeError, match='recover'):
            operation()


@pytest.mark.parametrize('tampering', ['pointer', 'launcher', 'legacy'])
def test_modified_selected_route_is_not_reported_verified(shared_runtime, tampering):
    app = shared_runtime
    revision = app.prepare('HEAD')
    app.activate(revision)
    if tampering == 'pointer':
        (app.root / 'current').unlink()
    elif tampering == 'launcher':
        (app.root / 'launch.sh').write_text('changed')
    else:
        (app.root / 'src/mcp_obsidian/__init__.py').write_text('changed')
    if tampering != 'legacy':
        assert app.status()['integrity'] == 'invalid'
    with pytest.raises(deployment.SharedRuntimeError):
        app.rollback()


def test_preflight_failure_never_creates_journal(shared_runtime, monkeypatch):
    app = shared_runtime
    revision = app.prepare('HEAD')
    monkeypatch.setattr(app, '_probe', lambda *args, **kwargs:
        (_ for _ in ()).throw(deployment.SharedRuntimeError('preflight failed')))
    with pytest.raises(deployment.SharedRuntimeError):
        app.activate(revision)
    assert not app.status()['pending'] and not (app.root / 'launch.sh').exists()


def test_launcher_keeps_running_environment_after_selection_changes(shared_runtime, monkeypatch):
    app = shared_runtime
    original_build = app._build
    def build(release):
        original_build(release)
        entry = release / '.venv/bin/mcp-obsidian'
        entry.write_text('#!/bin/sh\npwd\nread value\nprintf "%s\\n" "$value"\n')
    monkeypatch.setattr(app, '_build', build)
    first = app.prepare('HEAD')
    app.activate(first)
    monkeypatch.setenv('PYTHONPATH', '/wrong')
    monkeypatch.setenv('UV_PROJECT_ENVIRONMENT', '/wrong')
    process = subprocess.Popen([str(app.root / 'launch.sh')], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert process.stdout.readline().decode().strip() == str(app.root / 'releases' / first)
        second = second_release(app)
        app.activate(second)
        assert process.poll() is None
        output, _ = process.communicate(b'still-first\n', timeout=5)
        assert output == b'still-first\n'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_cli_status_needs_no_credentials_and_masks_failures(tmp_path, capsys):
    root = tmp_path / 'runtime'
    root.mkdir()
    assert deployment.main(['--root', str(root), '--repo', str(tmp_path), 'status']) == 0
    assert json.loads(capsys.readouterr().out)['selected'] == 'legacy'
    assert deployment.main(['--root', str(root), '--repo', str(tmp_path), 'activate', 'bad']) == 1
    output = capsys.readouterr()
    assert 'error' in output.err and 'Traceback' not in output.err


def test_no_previous_target_has_fixed_error(shared_runtime):
    with pytest.raises(deployment.SharedRuntimeError, match='previous'):
        shared_runtime.rollback()


def test_probe_skips_partial_environments(shared_runtime):
    app = shared_runtime
    app.prepare('HEAD')
    partial = app.root / 'releases' / ('0' * 40)
    partial.mkdir()
    assert app.probe()['ok'] is True


def test_cli_rejects_unused_revision_before_changing_selection(shared_runtime, monkeypatch, capsys):
    app = shared_runtime
    revision = app.prepare('HEAD')
    app.activate(revision)
    monkeypatch.setattr(deployment, 'SharedDeployment', lambda **kwargs: app)
    assert deployment.main(['rollback', 'ignored-revision']) == 1
    assert app.status()['selected'] == revision
    assert 'error' in capsys.readouterr().err


def test_probe_uses_prepared_python_and_masks_bad_subprocess_output(shared_runtime, monkeypatch):
    app = shared_runtime
    revision = app.prepare('HEAD')
    app.python = '/unavailable/operator/python'
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return b'{"ok":true,"tools":15,"vault_entries":0,"recent_changes":0}'
    monkeypatch.setattr(app, '_run', run)
    result = deployment.SharedDeployment._probe(app, revision)
    assert result['ok'] is True
    assert calls[0][0] == app.root / 'releases' / revision / '.venv/bin/python'
    monkeypatch.setattr(app, '_run', lambda *args, **kwargs: b'selected-sentinel-key')
    with pytest.raises(deployment.SharedRuntimeError) as error:
        deployment.SharedDeployment._probe(app, revision)
    assert 'sentinel' not in str(error.value)


def real_package_environment(release):
    import venv
    venv.create(release / '.venv', clear=True, with_pip=False, symlinks=True)
    python = release / '.venv/bin/python'
    site = Path(subprocess.check_output([str(python), '-I', '-c',
        'import sysconfig; print(sysconfig.get_paths()["purelib"])']).decode().strip())
    shutil.copytree(release / 'src/mcp_obsidian', site / 'mcp_obsidian')
    metadata = site / 'mcp_obsidian-0.2.2.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text('Name: mcp-obsidian\nVersion: 0.2.2\n')
    return python, site


def write_python_entry(entry, python, body):
    # Match installer wrappers for interpreter paths containing spaces.
    entry.write_text('#!/bin/sh\n\'\'\'exec\' ' + shlex.quote(str(python))
        + ' "$0" "$@"\n\' \'\'\'\n' + body)
    entry.chmod(0o700)


def test_real_probe_preserves_prepared_source_inventory(shared_runtime, monkeypatch):
    import sysconfig
    from tests.test_shared_probe import TOOLS
    app = shared_runtime
    scripts = Path(__file__).parents[1] / 'scripts'
    for name in ['probe_shared.py', 'shared_connection.py', 'probe_gateway.py']:
        shutil.copy(scripts / name, app.repo / 'scripts' / name)
    git(app.repo, 'commit', '-qam', 'real probe scripts')

    def build(release):
        python, site = real_package_environment(release)
        # Reuse only installed test dependencies; no network or real Vault.
        (site / 'test-dependencies.pth').write_text(sysconfig.get_paths()['purelib'] + '\n')
        entry = release / '.venv/bin/mcp-obsidian'
        write_python_entry(entry, python, 'import json, sys\ntools = ' + repr(sorted(TOOLS)) + '''
for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg: continue
    if msg['method'] == 'initialize':
        result = {'protocolVersion': msg['params']['protocolVersion'], 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'mcp-obsidian', 'version': 'test'}}
    elif msg['method'] == 'tools/list':
        result = {'tools': [{'name': t, 'inputSchema': {'type': 'object'}} for t in tools]}
    elif msg['method'] == 'tools/call':
        assert msg['params']['name'] in ('obsidian_list_files_in_vault', 'obsidian_get_recent_changes')
        result = {'content': [{'type': 'text', 'text': '[]'}], 'isError': False}
    else: raise RuntimeError('unexpected method')
    print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}), flush=True)
''')

    monkeypatch.setattr(app, '_build', build)
    monkeypatch.setattr(app, '_probe', deployment.SharedDeployment._probe.__get__(app))
    revision = app.prepare('HEAD')
    release, _ = app._manifest(revision)
    assert not list((release / 'scripts').rglob('*.pyc'))
    assert app.prepare(revision) == revision


@pytest.mark.parametrize('variable', ['PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__', 'PYTHONPLATLIBDIR'])
def test_real_launcher_ignores_python_startup_overrides(shared_runtime, tmp_path, monkeypatch, variable):
    import venv
    app = shared_runtime
    def build(release):
        python, _ = real_package_environment(release)
        entry = release / '.venv/bin/mcp-obsidian'
        write_python_entry(entry, python, 'import json, sys\n'
            + 'print(json.dumps({"prefix": sys.prefix, "executable": sys.executable}))\n')
    monkeypatch.setattr(app, '_build', build)
    revision = app.prepare('HEAD')
    release = app.root / 'releases' / revision
    python = release / '.venv/bin/python'
    app.activate(revision)
    other = tmp_path / 'other-environment'
    venv.create(other, with_pip=False, symlinks=True)
    env = deployment.clean_environment({})
    env[variable] = str(other / 'bin/python') if variable != 'PYTHONPLATLIBDIR' else 'missing-library'
    result = subprocess.run([str(app.root / 'launch.sh')], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'prefix': str(release / '.venv'), 'executable': str(python)}


@pytest.mark.parametrize('symlink_parent', [False, True])
def test_symlinked_runtime_location_can_activate_and_rollback(shared_runtime, tmp_path, monkeypatch, symlink_parent):
    original = shared_runtime
    alias = tmp_path / 'alias'
    alias.symlink_to(original.root.parent if symlink_parent else original.root, target_is_directory=True)
    root = alias / original.root.name if symlink_parent else alias
    app = deployment.SharedDeployment(root, original.repo, client_config=original.client_config,
        server=original.server, legacy_cwd=original.legacy_cwd)
    monkeypatch.setattr(app, '_build', original._build)
    monkeypatch.setattr(app, '_probe', original._probe)
    revision = app.prepare('HEAD')
    app.activate(revision)
    assert app.status()['integrity'] == 'verified'
    app.rollback()
    assert app.status()['selected'] == 'legacy' and app.status()['integrity'] == 'verified'


@pytest.mark.parametrize('route', ['cwd-relative', 'profile-path', 'relative-profile-path'])
def test_legacy_command_resolves_in_client_context(shared_runtime, monkeypatch, tmp_path, route):
    app = shared_runtime
    client_bin = app.root / 'client-bin'
    ambient_bin = tmp_path / 'ambient-bin'
    client_bin.mkdir()
    ambient_bin.mkdir()
    for directory, label in [(client_bin, 'client'), (ambient_bin, 'ambient')]:
        entry = directory / 'original-server'
        entry.write_text('#!/bin/sh\nprintf "' + label + '\\n"\n')
        entry.chmod(0o700)
    data = json.loads(app.client_config.read_text())
    profile = data['mcpServers']['obsidian']
    profile['cwd'] = str(app.root)
    profile['command'] = './client-bin/original-server' if route == 'cwd-relative' else 'original-server'
    if route != 'cwd-relative':
        profile['env']['PATH'] = str(client_bin) if route == 'profile-path' else 'client-bin'
    app.client_config.write_text(json.dumps(data))
    monkeypatch.setenv('PATH', str(ambient_bin))
    monkeypatch.chdir(tmp_path)
    with app._lock():
        app._record_legacy()
    record = json.loads((app.control / 'legacy.json').read_text())
    assert Path(record['command']) == client_bin / 'original-server'
    launcher = app.root / 'launch.sh'
    launcher.write_bytes(app._launcher())
    launcher.chmod(0o700)
    assert subprocess.check_output([str(launcher)]) == b'client\n'


@pytest.mark.parametrize('replacement', ['', '#!/bin/sh\nprintf "different\\n"\n'])
def test_changed_executable_wrapper_invalidates_prepared_release(shared_runtime, replacement):
    app = shared_runtime
    revision = app.prepare('HEAD')
    app.activate(revision)
    entry = app.root / 'releases' / revision / '.venv/bin/mcp-obsidian'
    entry.write_text(replacement)
    assert entry.stat().st_mode & 0o111
    assert app.status()['integrity'] == 'invalid'
    with pytest.raises(deployment.SharedRuntimeError, match='entry point'):
        app._manifest(revision)
    with pytest.raises(deployment.SharedRuntimeError, match='entry point'):
        app.prepare(revision)


@pytest.mark.parametrize('override', ['repository', 'work-tree', 'index'])
def test_git_overrides_cannot_redirect_preparation(shared_runtime, tmp_path, monkeypatch, override):
    app = shared_runtime
    expected = git(app.repo, 'rev-parse', 'HEAD')
    foreign = tmp_path / 'foreign-repository'
    shutil.copytree(app.repo, foreign)
    (foreign / 'src/mcp_obsidian/__init__.py').write_text('VERSION = "foreign"\n')
    git(foreign, 'commit', '-qam', 'foreign source')
    if override == 'repository':
        monkeypatch.setenv('GIT_DIR', str(foreign / '.git'))
        monkeypatch.setenv('GIT_WORK_TREE', str(foreign))
    elif override == 'work-tree':
        monkeypatch.setenv('GIT_WORK_TREE', str(foreign))
    else:
        monkeypatch.setenv('GIT_INDEX_FILE', str(foreign / '.git/index'))
    revision = app.prepare('HEAD')
    assert revision == expected
    release, _ = app._manifest(revision)
    assert (release / 'src/mcp_obsidian/__init__.py').read_text() == 'VERSION = 1\n'


def test_legacy_launcher_uses_same_uv_environment_as_probe(shared_runtime):
    import os
    app = shared_runtime
    legacy = app.root / 'legacy-command'
    legacy.write_text('#!/bin/sh\nprintf "%s|%s|%s\\n" '
        '"${UV_INDEX-unset}" "${UV_CACHE_DIR-unset}" "${UV_PYTHON-unset}"\n')
    revision = app.prepare('HEAD')
    app.activate(revision)
    app.rollback()
    profile = app._profile()
    overrides = {'UV_INDEX': 'wrong-index', 'UV_CACHE_DIR': '/wrong-cache', 'UV_PYTHON': '/wrong-python'}
    profile.env.update(overrides)
    assert not set(overrides) & deployment.clean_environment(profile.env).keys()
    env = {**os.environ, **profile.env}
    assert subprocess.check_output([str(app.root / 'launch.sh')], env=env) == b'unset|unset|unset\n'
