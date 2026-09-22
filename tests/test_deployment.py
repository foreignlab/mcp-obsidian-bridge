import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest


@pytest.fixture
def deploy(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('deploy', Path(__file__).parents[1] / 'scripts/deploy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    for key, value in [('user.name', 'Test'), ('user.email', 'test@example.invalid')]:
        subprocess.run(['git', '-C', str(repo), 'config', key, value], check=True)
    (repo / 'code.py').write_text('version = 1\n')
    (repo / 'uv.lock').write_text('locked\n')
    (repo / 'chatgpt').mkdir()
    (repo / 'chatgpt/gateway.py').write_text('pass\n')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'initial'], check=True)
    root = tmp_path / 'runtime'
    root.mkdir()
    (root / 'launch.sh').write_text('#!/bin/sh\n# legacy launcher\nexit 0\n')
    (root / 'launch.sh').chmod(0o700)
    (root / 'connection.json').write_text('{"secret":"never-copy"}')
    (root / 'connection.json').chmod(0o600)
    app = module.Deployment(root, repo)

    def build(release):
        python = release / '.venv/bin/python'
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text('#!/bin/sh\nexit 0\n')
        python.chmod(0o700)

    monkeypatch.setattr(app, '_build', build)
    monkeypatch.setattr(app, '_probe', lambda *args, **kwargs: {'ok': True})
    monkeypatch.setattr(app, '_restart', lambda: None)
    monkeypatch.setattr(app, '_wait_healthy', lambda: None)
    return module, app


def test_prepare_is_locked_and_does_not_switch_launcher(deploy):
    _, app = deploy
    before = (app.root / 'launch.sh').read_bytes()
    revision = app.prepare()
    release = app.root / 'releases' / revision
    assert (release / 'code.py').read_text() == 'version = 1\n'
    assert (release / 'chatgpt/connection.json').is_symlink()
    assert (release / 'chatgpt/connection.json').resolve() == app.root / 'connection.json'
    assert 'never-copy' not in (release / 'release.json').read_text()
    assert (app.root / 'launch.sh').read_bytes() == before
    assert not (app.root / 'current').exists()
    assert app.prepare() == revision


def test_dirty_source_is_rejected_before_build(deploy):
    module, app = deploy
    (app.repo / 'untracked.txt').write_text('not committed')
    with pytest.raises(module.DeploymentError, match='clean'):
        app.prepare()
    assert not (app.root / 'current').exists()


def test_modified_release_cannot_be_activated(deploy):
    module, app = deploy
    revision = app.prepare()
    (app.root / 'releases' / revision / 'code.py').write_text('tampered')
    with pytest.raises(module.DeploymentError, match='integrity'):
        app.activate(revision)
    assert not (app.root / 'current').exists()


def test_failed_prepare_is_not_activatable_but_can_resume(deploy, monkeypatch):
    module, app = deploy
    with monkeypatch.context() as patch:
        patch.setattr(app, '_probe', lambda *args, **kwargs: (_ for _ in ()).throw(module.DeploymentError('probe failed')))
        with pytest.raises(module.DeploymentError, match='probe failed'):
            app.prepare()
    revision = app._revision('HEAD')
    with pytest.raises(module.DeploymentError, match='prepared'):
        app.activate(revision)
    assert app.prepare() == revision


def test_activation_and_legacy_rollback_preserve_original_launcher(deploy):
    _, app = deploy
    legacy = (app.root / 'launch.sh').read_bytes()
    revision = app.prepare()
    app.activate(revision)
    assert (app.root / 'current').resolve() == app.root / 'releases' / revision
    assert app.status()['active'] == revision
    assert app.status()['previous'] == 'legacy'
    app.rollback()
    assert (app.root / 'launch.sh').read_bytes() == legacy
    assert not (app.root / 'current').exists()
    assert app.status()['active'] == 'legacy'


def test_health_failure_restores_old_launcher_and_records_recovery(deploy, monkeypatch):
    module, app = deploy
    legacy = (app.root / 'launch.sh').read_bytes()
    revision = app.prepare()
    outcomes = iter([module.DeploymentError('unhealthy'), None])

    def health():
        result = next(outcomes)
        if result:
            raise result

    monkeypatch.setattr(app, '_wait_healthy', health)
    with pytest.raises(module.DeploymentError, match='previous deployment restored'):
        app.activate(revision)
    assert (app.root / 'launch.sh').read_bytes() == legacy
    assert not (app.root / 'current').exists()
    assert app.status()['pending'] is False


def test_preflight_failure_leaves_active_unchanged(deploy, monkeypatch):
    module, app = deploy
    revision = app.prepare()
    monkeypatch.setattr(app, '_probe', lambda *args, **kwargs: (_ for _ in ()).throw(module.DeploymentError('probe failed')))
    with pytest.raises(module.DeploymentError, match='probe failed'):
        app.activate(revision)
    assert not (app.root / 'current').exists()
    assert app.status()['pending'] is False


def test_subsequent_release_rollback_restores_previous_commit(deploy):
    _, app = deploy
    first = app.prepare()
    app.activate(first)
    (app.repo / 'code.py').write_text('version = 2\n')
    subprocess.run(['git', '-C', str(app.repo), 'commit', '-qam', 'second'], check=True)
    second = app.prepare()
    app.activate(second)
    assert app.status()['previous'] == first
    app.rollback()
    assert app.status()['active'] == first
    assert (app.root / 'current').resolve().name == first


def test_failed_recovery_keeps_journal_for_explicit_recover(deploy, monkeypatch):
    module, app = deploy
    revision = app.prepare()
    with monkeypatch.context() as patch:
        patch.setattr(app, '_wait_healthy', lambda: (_ for _ in ()).throw(module.DeploymentError('unhealthy')))
        with pytest.raises(module.DeploymentError, match='recover'):
            app.activate(revision)
    assert app.status()['pending'] is True
    app.recover()
    assert app.status()['pending'] is False
    assert app.status()['active'] == 'legacy'


def test_deploy_lock_blocks_concurrent_transition(deploy):
    module, app = deploy
    with app._lock():
        with pytest.raises(module.DeploymentError, match='[Aa]nother deployment'):
            app.prepare()


def test_prepare_can_retry_after_interrupted_extraction(deploy, monkeypatch):
    module, app = deploy
    extract = module.extract_archive

    def interrupted(data, destination):
        extract(data, destination)
        raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(module, 'extract_archive', interrupted)
        with pytest.raises(KeyboardInterrupt):
            app.prepare()
    assert app.prepare() == app._revision('HEAD')


def test_launcher_and_recovery_probes_use_prepared_environment(deploy, monkeypatch):
    module, app = deploy
    revision = app.prepare()
    release = app.root / 'releases' / revision
    app.python = '/unavailable/system/python'
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return b'{"ok": true}'

    monkeypatch.setattr(app, '_run', run)
    monkeypatch.setattr(app, '_probe', module.Deployment._probe.__get__(app))
    app.activate(revision)
    app.rollback()
    with monkeypatch.context() as patch:
        patch.setattr(app, '_wait_healthy', lambda: (_ for _ in ()).throw(module.DeploymentError('unhealthy')))
        with pytest.raises(module.DeploymentError, match='recover'):
            app.activate(revision)
    app.recover()
    assert len(commands) >= 5
    assert all(command[:2] == [release / '.venv/bin/python', release / 'scripts/probe_gateway.py']
               for command in commands)


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'chatgpt/connection.json', '.venv/python'])
def test_unsafe_archive_members_are_rejected(deploy, tmp_path, name):
    module, _ = deploy
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        item = tarfile.TarInfo(name)
        item.size = 1
        archive.addfile(item, io.BytesIO(b'x'))
    with pytest.raises(module.DeploymentError, match='archive'):
        module.extract_archive(stream.getvalue(), tmp_path / 'extract')
