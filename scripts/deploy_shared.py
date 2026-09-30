#!/usr/bin/env python3
"""Commit-addressed shared stdio environments with verified, recoverable selection."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

from shared_connection import SharedRuntimeError, clean_environment, read_client_profile

SHA = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2) + '\n').encode())


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise SharedRuntimeError('Invalid shared deployment metadata') from None


def file_info(path):
    if path.is_symlink() or not path.is_file():
        raise SharedRuntimeError('Deployment integrity verification failed')
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'mode': path.stat().st_mode & 0o777}


def extract_source(data, destination):
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = archive.getmembers()
        seen = set()
        for member in members:
            path = PurePosixPath(member.name)
            private = {'.git', '.venv', 'connection.json', 'runtime-api-key', 'tunnel', 'release.json'}
            if (path.is_absolute() or '..' in path.parts or private.intersection(path.parts)
                    or any(part.startswith('.env') or part.endswith(('.key', '.pem', '.crt', '.p12', '.pfx')) for part in path.parts)
                    or member.name in seen or not (member.isfile() or member.isdir())):
                raise SharedRuntimeError('Unsafe source archive')
            seen.add(member.name)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                atomic_write(target, archive.extractfile(member).read(), member.mode & 0o755)


class SharedDeployment:
    def __init__(self, root, repo, *, client_config=None, server=None, uv=None, python=None, legacy_cwd=None):
        self.root = Path(root).absolute()
        self.repo = Path(repo).absolute()
        self.client_config = Path(client_config).absolute() if client_config else None
        self.server = server
        self.uv = uv or shutil.which('uv')
        self.python = python or sys.executable
        self.legacy_cwd = Path(legacy_cwd).absolute() if legacy_cwd else None
        self.control = self.root / 'deploy-shared'

    @contextmanager
    def _lock(self):
        self.control.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.control.is_symlink():
            raise SharedRuntimeError('Invalid shared control directory')
        with (self.control / 'lock').open('a') as lock:
            os.chmod(lock.name, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SharedRuntimeError('Another shared deployment is running') from None
            yield

    def _run(self, args, *, timeout=120):
        try:
            result = subprocess.run([str(a) for a in args], cwd=self.repo,
                env=clean_environment({}), capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise SharedRuntimeError('Deployment subprocess failed or timed out; output suppressed') from None
        if result.returncode:
            raise SharedRuntimeError('Deployment subprocess failed; output suppressed')
        return result.stdout

    def _profile(self):
        if not self.client_config or not self.server:
            raise SharedRuntimeError('Client configuration and server name are required')
        return read_client_profile(self.client_config, self.server)

    def _no_pending(self):
        if (self.control / 'pending.json').exists():
            raise SharedRuntimeError('Interrupted transition exists; run recover')

    def _revision(self, revision):
        value = self._run(['git', 'rev-parse', '--verify', '--end-of-options', f'{revision}^{{commit}}']).decode().strip()
        if not SHA.fullmatch(value):
            raise SharedRuntimeError('Invalid source revision')
        return value

    def _inventory(self, directory):
        files = {}
        for path in directory.rglob('*'):
            relative = path.relative_to(directory)
            if relative.parts[0] == '.venv' or str(relative) == 'release.json':
                continue
            if path.is_symlink():
                raise SharedRuntimeError('Deployment integrity verification failed')
            if path.is_file():
                files[str(relative)] = file_info(path)
        return files

    def _legacy_inventory(self):
        paths = [self.root / 'pyproject.toml', self.root / 'uv.lock']
        package = self.root / 'src/mcp_obsidian'
        if package.is_symlink() or not package.is_dir():
            raise SharedRuntimeError('Legacy source integrity verification failed')
        paths += list(package.rglob('*.py'))
        return {str(path.relative_to(self.root)): file_info(path) for path in paths}

    def _record_legacy(self):
        if (self.control / 'legacy.json').exists():
            self._check_legacy()
            return
        if (self.root / 'launch.sh').exists() or (self.root / 'current').is_symlink():
            raise SharedRuntimeError('Existing launcher is not a registered legacy route')
        profile = self._profile()
        cwd = profile.cwd or self.legacy_cwd
        if not cwd or not cwd.is_dir():
            raise SharedRuntimeError('Explicit legacy working directory is required')
        command = profile.command if Path(profile.command).is_absolute() else shutil.which(profile.command)
        if not command:
            raise SharedRuntimeError('Legacy executable is unavailable')
        write_json(self.control / 'legacy.json', {'command': command, 'args': profile.args,
            'cwd': str(cwd), 'files': self._legacy_inventory()})

    def _check_legacy(self):
        legacy = read_json(self.control / 'legacy.json')
        if legacy.get('files') != self._legacy_inventory():
            raise SharedRuntimeError('Legacy source integrity verification failed')
        return legacy

    def _build(self, release):
        if not self.uv:
            raise SharedRuntimeError('uv is unavailable')
        self._run([self.uv, 'sync', '--locked', '--offline', '--no-dev', '--no-editable',
                   '--python', self.python, '--project', release], timeout=300)

    def _installed_inventory(self, release):
        script = '''import hashlib, importlib.metadata, json, pathlib, sys
env = pathlib.Path(sys.argv[1]).resolve()
package = pathlib.Path(importlib.metadata.distribution('mcp-obsidian').locate_file('mcp_obsidian'))
assert not package.is_symlink() and package.resolve().is_relative_to(env)
files = {}
for path in package.rglob('*.py'):
    assert not path.is_symlink() and path.resolve().is_relative_to(env)
    files['mcp_obsidian/' + str(path.relative_to(package))] = hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(files))
'''
        try:
            return json.loads(self._run([release / '.venv/bin/python', '-I', '-c', script, release / '.venv']))
        except (ValueError, SharedRuntimeError):
            raise SharedRuntimeError('Installed package integrity verification failed') from None

    def _verify_installed(self, release, manifest):
        expected = {name.removeprefix('src/'): info['sha256'] for name, info in manifest['files'].items()
                    if name.startswith('src/mcp_obsidian/') and name.endswith('.py')}
        if not expected or self._installed_inventory(release) != expected:
            raise SharedRuntimeError('Installed package integrity verification failed')
        if not os.access(release / '.venv/bin/mcp-obsidian', os.X_OK):
            raise SharedRuntimeError('Installed entry point integrity verification failed')

    def _manifest(self, revision, *, require_prepared=True):
        if not SHA.fullmatch(revision):
            raise SharedRuntimeError('Use the full prepared source SHA')
        release = self.root / 'releases' / revision
        try:
            manifest = read_json(release / 'release.json')
            if release.is_symlink() or manifest['revision'] != revision or manifest['files'] != self._inventory(release):
                raise ValueError()
        except (ValueError, KeyError, TypeError, OSError):
            raise SharedRuntimeError('Source integrity verification failed') from None
        if require_prepared:
            if manifest.get('status') != 'prepared':
                raise SharedRuntimeError('Release is not prepared')
            self._verify_installed(release, manifest)
        return release, manifest

    def _probe(self, target, *, launcher=False, probe_revision=None):
        self._profile()
        revision = probe_revision or target
        release, _ = self._manifest(revision, require_prepared=False)
        args = [release / '.venv/bin/python', release / 'scripts/probe_shared.py',
                '--client-config', self.client_config, '--server', self.server]
        if launcher:
            args += ['--launcher', self.root / 'launch.sh']
        elif target == 'legacy':
            self._check_legacy()
            args += ['--launch-record', self.control / 'legacy.json']
        else:
            args += ['--executable', release / '.venv/bin/mcp-obsidian', '--cwd', release]
        try:
            result = json.loads(self._run(args, timeout=60))
            if (set(result) != {'ok', 'tools', 'vault_entries', 'recent_changes'} or result['ok'] is not True
                    or result['tools'] != 15 or any(type(result[key]) is not int or result[key] < 0
                        for key in ('tools', 'vault_entries', 'recent_changes')) or result['recent_changes'] > 3):
                raise ValueError()
            return result
        except (ValueError, TypeError, SharedRuntimeError):
            raise SharedRuntimeError('Shared runtime probe failed; private details suppressed') from None

    def prepare(self, revision):
        with self._lock():
            self._no_pending()
            if self._run(['git', 'status', '--porcelain']).strip():
                raise SharedRuntimeError('Source checkout must be clean before preparation')
            self._profile()
            revision = self._revision(revision)
            self._record_legacy()
            release = self.root / 'releases' / revision
            if release.exists():
                release, manifest = self._manifest(revision, require_prepared=False)
            else:
                release.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='.prepare-', dir=release.parent) as temporary:
                    staging = Path(temporary) / 'source'
                    staging.mkdir(mode=0o700)
                    extract_source(self._run(['git', 'archive', revision]), staging)
                    manifest = {'revision': revision, 'status': 'preparing', 'files': self._inventory(staging)}
                    write_json(staging / 'release.json', manifest)
                    os.rename(staging, release)
                    sync_directory(release.parent)
            if manifest['status'] != 'prepared':
                self._build(release)
            self._verify_installed(release, manifest)
            summary = self._probe(revision)
            manifest.update(status='prepared', prepared_at=datetime.now(timezone.utc).isoformat(), probe=summary)
            write_json(release / 'release.json', manifest)
            self._manifest(revision)
            return revision

    def _launcher(self):
        legacy = self._check_legacy()
        command = shlex.join([legacy['command'], *legacy['args']])
        return f'''#!/bin/sh
set -eu
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV
base={shlex.quote(str(self.root))}
if [ -L "$base/current" ]; then
    release=$(CDPATH= cd -- "$base/current" && pwd -P)
    cd "$release"
    exec "$release/.venv/bin/mcp-obsidian"
fi
cd {shlex.quote(legacy['cwd'])}
exec {command}
'''.encode()

    def _state(self):
        path = self.control / 'state.json'
        state = read_json(path) if path.exists() else {'selected': 'legacy', 'previous': None}
        if (set(state) != {'selected', 'previous'}
                or any(value is not None and value != 'legacy' and (not isinstance(value, str) or not SHA.fullmatch(value))
                       for value in state.values()) or state['selected'] is None):
            raise SharedRuntimeError('Invalid shared deployment state')
        return state

    def status(self):
        state = self._state()
        integrity = 'unmanaged'
        if (self.control / 'legacy.json').exists():
            try:
                if state['selected'] == 'legacy':
                    self._check_legacy()
                else:
                    self._manifest(state['selected'])
                integrity = 'verified'
            except SharedRuntimeError:
                integrity = 'invalid'
        return {**state, 'pending': (self.control / 'pending.json').exists(), 'integrity': integrity}
