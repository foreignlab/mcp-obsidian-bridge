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
import uuid

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
        self.root = Path(root).resolve()
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
        env = {key: value for key, value in clean_environment({}).items()
               if not key.startswith('GIT_')}
        try:
            result = subprocess.run([str(a) for a in args], cwd=self.repo,
                env=env, capture_output=True, timeout=timeout)
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
        if Path(profile.command).is_absolute():
            command = profile.command
        elif '/' in profile.command:
            command = str(cwd / profile.command)
        else:
            search_path = clean_environment(profile.env).get('PATH', os.defpath)
            paths = [str(Path(part)) if Path(part).is_absolute() else str(cwd / (part or '.'))
                     for part in search_path.split(os.pathsep)]
            command = shutil.which(profile.command, path=os.pathsep.join(paths))
        if not command or not Path(command).is_file() or not os.access(command, os.X_OK):
            raise SharedRuntimeError('Legacy executable is unavailable')
        write_json(self.control / 'legacy.json', {'command': command, 'args': profile.args,
            'executable': self._legacy_executable_info(command),
            'cwd': str(cwd), 'files': self._legacy_inventory()})

    def _legacy_executable_info(self, command):
        try:
            path = Path(command)
            if not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError()
            return file_info(path.resolve())
        except (OSError, ValueError, SharedRuntimeError):
            raise SharedRuntimeError('Legacy executable integrity verification failed') from None

    def _check_legacy(self):
        legacy = read_json(self.control / 'legacy.json')
        if legacy.get('files') != self._legacy_inventory():
            raise SharedRuntimeError('Legacy source integrity verification failed')
        if legacy.get('executable') != self._legacy_executable_info(legacy['command']):
            raise SharedRuntimeError('Legacy executable integrity verification failed')
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
        entrypoint = release / '.venv/bin/mcp-obsidian'
        if (not os.access(entrypoint, os.X_OK) or entrypoint.is_symlink()
                or manifest.get('entrypoint') != file_info(entrypoint)):
            raise SharedRuntimeError('Installed entry point integrity verification failed')
        if manifest.get('runtime') != self._runtime_inventory(release):
            raise SharedRuntimeError('Installed environment integrity verification failed')

    def _runtime_inventory(self, release):
        environment = release / '.venv'
        if environment.is_symlink() or not environment.is_dir():
            raise SharedRuntimeError('Installed environment integrity verification failed')
        files = {}
        for path in environment.rglob('*'):
            relative = path.relative_to(environment)
            if '__pycache__' in relative.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            if path.is_symlink():
                files[str(relative)] = {'symlink': hashlib.sha256(os.readlink(path).encode()).hexdigest(),
                    'mode': path.lstat().st_mode & 0o777}
                if path.is_file():
                    files[str(relative)]['target'] = file_info(path.resolve())
                elif not path.is_dir() or not path.resolve().is_relative_to(environment.resolve()):
                    raise SharedRuntimeError('Installed environment integrity verification failed')
            elif path.is_file():
                files[str(relative)] = file_info(path)
        return files

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
        args = [release / '.venv/bin/python', '-B', release / 'scripts/probe_shared.py',
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
                manifest['entrypoint'] = file_info(release / '.venv/bin/mcp-obsidian')
                manifest['runtime'] = self._runtime_inventory(release)
            self._verify_installed(release, manifest)
            summary = self._probe(revision)
            manifest.update(status='prepared', prepared_at=datetime.now(timezone.utc).isoformat(), probe=summary)
            write_json(release / 'release.json', manifest)
            self._manifest(revision)
            self._history('prepared', revision=revision)
            return revision

    def _launcher(self):
        legacy = read_json(self.control / 'legacy.json')
        command = shlex.join([legacy['command'], *legacy['args']])
        return f'''#!/bin/sh
set -eu
for variable in $(/usr/bin/env | /usr/bin/awk -F= '$1 ~ /^(PYTHON[A-Za-z0-9_]*|UV_[A-Za-z0-9_]*)$/ {{print $1}}'); do
    unset "$variable"
done
unset __PYVENV_LAUNCHER__ VIRTUAL_ENV
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
        return self._validate_state(state)

    def _validate_state(self, state):
        if (not isinstance(state, dict) or set(state) != {'selected', 'previous'}
                or any(value is not None and value != 'legacy' and (not isinstance(value, str) or not SHA.fullmatch(value))
                       for value in state.values()) or state['selected'] is None):
            raise SharedRuntimeError('Invalid shared deployment state')
        return state

    def _check_selected(self):
        state = self._state()
        current, launcher = self.root / 'current', self.root / 'launch.sh'
        if launcher.is_symlink() or (launcher.exists() and
                (launcher.read_bytes() != self._launcher() or launcher.stat().st_mode & 0o777 != 0o700)):
            raise SharedRuntimeError('Selected launcher integrity verification failed')
        if state['selected'] == 'legacy':
            self._check_legacy()
            if current.exists() or current.is_symlink() or ((self.control / 'state.json').exists() and not launcher.is_file()):
                raise SharedRuntimeError('Selected pointer integrity verification failed')
        else:
            self._manifest(state['selected'])
            expected = self.root / 'releases' / state['selected']
            if not launcher.is_file() or not current.is_symlink() or current.resolve() != expected:
                raise SharedRuntimeError('Selected pointer integrity verification failed')
        return state

    def status(self):
        state = self._state()
        integrity = 'unmanaged'
        if (self.control / 'legacy.json').exists():
            try:
                self._check_selected()
                integrity = 'verified'
            except SharedRuntimeError:
                integrity = 'invalid'
        return {**state, 'pending': (self.control / 'pending.json').exists(), 'integrity': integrity}

    def _pointer(self, target):
        current = self.root / 'current'
        if target is None:
            if current.is_symlink():
                current.unlink()
        else:
            temporary = self.root / ('.current-' + uuid.uuid4().hex)
            try:
                temporary.symlink_to(target)
                os.replace(temporary, current)
            finally:
                temporary.unlink(missing_ok=True)
        sync_directory(self.root)

    def _history(self, event, **fields):
        entry = {'timestamp': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}
        with (self.control / 'history.jsonl').open('a') as output:
            os.chmod(output.name, 0o600)
            output.write(json.dumps(entry) + '\n')
            output.flush()
            os.fsync(output.fileno())

    def _snapshot(self, probe_revision):
        launcher, current = self.root / 'launch.sh', self.root / 'current'
        state_path = self.control / 'state.json'
        snapshot = {
            'state': read_json(state_path) if state_path.exists() else None,
            'target': os.readlink(current) if current.is_symlink() else None,
            'launcher': {'content': launcher.read_bytes().hex(), 'mode': launcher.stat().st_mode & 0o777}
                        if launcher.exists() else None,
            'probe_revision': probe_revision,
        }
        write_json(self.control / 'pending.json', snapshot)

    def _restore(self):
        snapshot = read_json(self.control / 'pending.json')
        try:
            state = self._validate_state(snapshot['state'] or {'selected': 'legacy', 'previous': None})
            target = snapshot['target']
            expected = None if state['selected'] == 'legacy' else str(self.root / 'releases' / state['selected'])
            if target != expected:
                raise ValueError()
            if not isinstance(snapshot['probe_revision'], str) or not SHA.fullmatch(snapshot['probe_revision']):
                raise ValueError()
            launcher = snapshot['launcher']
            if launcher is not None and (launcher['mode'] != 0o700 or bytes.fromhex(launcher['content']) != self._launcher()):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise SharedRuntimeError('Invalid recovery snapshot; journal retained') from None
        self._pointer(target)
        path = self.root / 'launch.sh'
        if launcher is None:
            path.unlink(missing_ok=True)
            sync_directory(self.root)
        else:
            atomic_write(path, bytes.fromhex(launcher['content']), launcher['mode'])
        if snapshot['state'] is None:
            (self.control / 'state.json').unlink(missing_ok=True)
            sync_directory(self.control)
        else:
            write_json(self.control / 'state.json', snapshot['state'])
        self._check_selected()
        revision = state['selected']
        if revision == 'legacy':
            revision = self._verification_revision(snapshot['probe_revision'])
        self._probe(state['selected'], launcher=launcher is not None, probe_revision=revision)

    def _clear_pending(self):
        (self.control / 'pending.json').unlink()
        sync_directory(self.control)

    def _transition(self, target):
        state = self._check_selected()
        if state['selected'] == target:
            raise SharedRuntimeError('Requested source is already selected')
        if target == 'legacy':
            self._check_legacy()
            probe_revision = state['selected']
        else:
            self._manifest(target)
            probe_revision = target
        self._probe(target, probe_revision=probe_revision)
        if state['selected'] == 'legacy':
            self._probe('legacy', probe_revision=probe_revision)
        self._snapshot(probe_revision)
        try:
            atomic_write(self.root / 'launch.sh', self._launcher(), 0o700)
            self._pointer(None if target == 'legacy' else self.root / 'releases' / target)
            self._probe(target, launcher=True, probe_revision=probe_revision)
            write_json(self.control / 'state.json', {'selected': target, 'previous': state['selected']})
            self._check_selected()
            self._history('selected', selected=target, previous=state['selected'])
            self._clear_pending()
        except Exception:
            try:
                self._restore()
                self._history('transition_failed_restored', selected=state['selected'])
                self._clear_pending()
            except Exception:
                raise SharedRuntimeError('Transition recovery incomplete; inspect status and run recover') from None
            raise SharedRuntimeError('Transition failed; previous route restored and verified') from None

    def activate(self, revision):
        with self._lock():
            self._no_pending()
            self._transition(revision)

    def rollback(self):
        with self._lock():
            self._no_pending()
            previous = self._state()['previous']
            if previous is None:
                raise SharedRuntimeError('No previous shared target is recorded')
            self._transition(previous)

    def recover(self):
        with self._lock():
            if not (self.control / 'pending.json').exists():
                raise SharedRuntimeError('No pending shared transition exists')
            self._restore()
            self._history('recovered', selected=self._state()['selected'])
            self._clear_pending()

    def _verification_revision(self, preferred=None):
        releases = self.root / 'releases'
        candidates = ([releases / preferred] if preferred else []) + sorted(releases.glob('*'))
        for candidate in candidates:
            if not SHA.fullmatch(candidate.name):
                continue
            try:
                self._manifest(candidate.name)
            except SharedRuntimeError:
                continue
            return candidate.name
        raise SharedRuntimeError('A prepared environment is required for verification')

    def probe(self):
        state = self._check_selected()
        revision = state['selected']
        if revision == 'legacy':
            revision = self._verification_revision()
        return self._probe(state['selected'], launcher=(self.root / 'launch.sh').exists(), probe_revision=revision)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / '.local/share/mcp-obsidian-tls')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--client-config', type=Path)
    parser.add_argument('--server')
    parser.add_argument('--legacy-cwd', type=Path)
    parser.add_argument('--uv')
    parser.add_argument('--python')
    parser.add_argument('command', choices=['prepare', 'activate', 'rollback', 'recover', 'status', 'probe'])
    parser.add_argument('revision', nargs='?')
    args = parser.parse_args(argv)
    options = vars(args).copy()
    command, revision = options.pop('command'), options.pop('revision')
    app = SharedDeployment(**options)
    try:
        if command not in ('prepare', 'activate') and revision is not None:
            raise SharedRuntimeError('This command does not accept a source revision')
        if command != 'status':
            app._profile()
        if command in ('prepare', 'activate'):
            if not revision:
                raise SharedRuntimeError('An explicit source revision is required')
            result = getattr(app, command)(revision)
            summary = {'prepared': result} if command == 'prepare' else app.status()
        elif command == 'probe':
            summary = app.probe()
        elif command == 'status':
            summary = app.status()
        else:
            getattr(app, command)()
            summary = app.status()
        print(json.dumps(summary))
    except Exception as error:
        message = str(error) if isinstance(error, SharedRuntimeError) else 'Shared deployment failed; private details suppressed'
        print(json.dumps({'error': message}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
