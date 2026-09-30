import hashlib
import importlib
import io
import json
from pathlib import Path
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
    venv.create(release / '.venv', clear=True, with_pip=False)
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
