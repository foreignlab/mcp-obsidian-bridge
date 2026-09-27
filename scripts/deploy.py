#!/usr/bin/env python3
"""Commit-addressed ChatGPT releases with serialized activation and rollback."""

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
import subprocess
import sys
import tarfile
import tempfile
import time


class DeploymentError(Exception):
    pass


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path, data, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2) + '\n').encode())


def extract_archive(data, destination):
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            forbidden = {'.git', '.venv', 'connection.json', 'runtime-api-key', 'tunnel'}
            if (path.is_absolute() or '..' in path.parts or forbidden.intersection(path.parts)
                    or member.name == 'release.json' or not (member.isfile() or member.isdir())):
                raise DeploymentError('Unsafe archive member')
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                atomic_write(target, archive.extractfile(member).read(), member.mode & 0o755)


class Deployment:
    def __init__(self, root, repo, uv='/opt/homebrew/bin/uv', python=None):
        self.root = Path(root).absolute()
        self.repo = Path(repo).absolute()
        self.uv = uv
        self.python = python or sys.executable
        self.control = self.root / 'deploy'
        self.label = f'gui/{os.getuid()}/com.foreignlab.obsidian-chatgpt-tunnel'
        self.old_pid = None

    @contextmanager
    def _lock(self):
        self.control.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.control / 'lock').open('a') as lock:
            os.chmod(lock.name, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DeploymentError('Another deployment is running; retry after it finishes') from None
            yield

    def _run(self, args, *, timeout=120):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('UV_') and key not in ('VIRTUAL_ENV', 'PYTHONPATH', 'PYTHONHOME')}
        try:
            result = subprocess.run([str(a) for a in args], cwd=self.repo,
                                    env=env, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise DeploymentError(f'{Path(args[0]).name} failed or timed out; output suppressed') from None
        if result.returncode:
            raise DeploymentError(f'{Path(args[0]).name} exited {result.returncode}; output suppressed')
        return result.stdout

    def _revision(self, revision):
        value = self._run(['git', 'rev-parse', '--verify', f'{revision}^{{commit}}']).decode().strip()
        if not re.fullmatch('[0-9a-f]{40,64}', value):
            raise DeploymentError('Invalid commit ID')
        return value

    def _no_pending(self):
        if (self.control / 'pending.json').exists():
            raise DeploymentError('Interrupted transition exists; inspect status and run recover')

    def _manifest(self, revision, require_prepared=True):
        if not re.fullmatch('[0-9a-f]{40,64}', revision):
            raise DeploymentError('Use the full prepared commit ID')
        release = self.root / 'releases' / revision
        try:
            manifest = json.loads((release / 'release.json').read_text())
            if manifest['revision'] != revision:
                raise ValueError()
            for name, info in manifest['files'].items():
                path = PurePosixPath(name)
                target = release / name
                if path.is_absolute() or '..' in path.parts or target.is_symlink():
                    raise ValueError()
                if digest(target) != info['sha256'] or target.stat().st_mode & 0o777 != info['mode']:
                    raise ValueError()
            config = release / 'chatgpt/connection.json'
            if not config.is_symlink() or config.resolve() != (self.root / 'connection.json').resolve():
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise DeploymentError('Release integrity verification failed') from None
        if require_prepared and (manifest['status'] != 'prepared' or not (release / '.venv/bin/python').is_file()):
            raise DeploymentError('Release is not prepared')
        return release, manifest

    def _build(self, release):
        self._run([self.uv, 'sync', '--locked', '--offline', '--no-dev', '--no-editable',
                   '--python', self.python, '--project', release], timeout=300)

    def _probe(self, release, *, launcher=False, legacy=False):
        python = release / '.venv/bin/python'
        script = release / 'scripts/probe_gateway.py'
        args = [python, script]
        if launcher:
            args += ['--launcher', self.root / 'launch.sh']
        else:
            args += ['--gateway', release / 'chatgpt/gateway.py']
        if legacy:
            args += ['--skip-recent']
        return json.loads(self._run(args, timeout=60))

    def prepare(self, revision='HEAD'):
        with self._lock():
            self._no_pending()
            if self._run(['git', 'status', '--porcelain']).strip():
                raise DeploymentError('Source checkout must be clean before preparation')
            config = self.root / 'connection.json'
            if not config.is_file() or config.stat().st_mode & 0o077:
                raise DeploymentError('Private connection.json is required (mode 0600)')
            revision = self._revision(revision)
            release = self.root / 'releases' / revision
            if release.exists():
                release, manifest = self._manifest(revision, require_prepared=False)
            else:
                archive = self._run(['git', 'archive', '--format=tar', revision])
                release.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='.prepare-', dir=release.parent) as temporary:
                    staging = Path(temporary) / 'source'
                    staging.mkdir(mode=0o700)
                    extract_archive(archive, staging)
                    manifest = {'revision': revision, 'status': 'preparing', 'files': {
                        str(path.relative_to(staging)): {'sha256': digest(path), 'mode': path.stat().st_mode & 0o777}
                        for path in staging.rglob('*') if path.is_file()
                    }}
                    (staging / 'chatgpt/connection.json').symlink_to(config)
                    write_json(staging / 'release.json', manifest)
                    os.rename(staging, release)
            if manifest['status'] != 'prepared':
                self._build(release)
            probe = self._probe(release)
            manifest.update(status='prepared', prepared_at=datetime.now(timezone.utc).isoformat(), probe=probe)
            write_json(release / 'release.json', manifest)
            self._manifest(revision)
            self._history('prepared', revision=revision)
            return revision

    def _launcher(self):
        return f'''#!/bin/sh
# Managed by obsidian-chatgpt-mcp/scripts/deploy.py
set -eu
base={shlex.quote(str(self.root))}
release=$(CDPATH= cd -- "$base/current" && pwd -P)
export OBSIDIAN_DIAGNOSTICS_DIR="$base/logs"
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV
cd "$release"
exec "$release/.venv/bin/python" "$release/chatgpt/gateway.py"
'''.encode()

    def status(self):
        path = self.control / 'state.json'
        state = json.loads(path.read_text()) if path.exists() else {'active': 'legacy', 'previous': None}
        return dict(state, pending=(self.control / 'pending.json').exists())

    def _check_active(self):
        state = self.status()
        launcher = self.root / 'launch.sh'
        if not launcher.is_file() or launcher.is_symlink():
            raise DeploymentError('Expected a regular launch.sh')
        current = self.root / 'current'
        if state['active'] == 'legacy':
            if current.exists() or current.is_symlink():
                raise DeploymentError('Unexpected current pointer in legacy state')
            backup = self.control / 'legacy-launch.sh'
            if backup.exists() and launcher.read_bytes() != backup.read_bytes():
                raise DeploymentError('Legacy launcher changed outside deployment manager')
        elif (not current.is_symlink() or current.resolve() != self.root / 'releases' / state['active']
              or launcher.read_bytes() != self._launcher()):
            raise DeploymentError('Active launcher or pointer changed outside deployment manager')
        return state

    def _pointer(self, target):
        current = self.root / 'current'
        if target is None:
            if current.is_symlink():
                current.unlink()
            return
        temporary = self.root / '.current-next'
        if temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(target)
        os.replace(temporary, current)

    def _restart(self):
        pid_file = self.root / 'tunnel/launchd.pid'
        try:
            self.old_pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            self.old_pid = None
        self._run(['/bin/launchctl', 'kickstart', '-k', self.label], timeout=30)

    def _wait_healthy(self):
        tunnel = self.root / 'tunnel'
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                health = json.loads(self._run([
                    tunnel / 'v0.0.14/tunnel-client', 'health',
                    '--url-file', tunnel / 'launchd-health.url', '--pid-file', tunnel / 'launchd.pid',
                    '--require-control-plane-poll', '--json',
                ], timeout=5))
                if health.get('result') == 'ok' and health.get('process', {}).get('pid') != self.old_pid:
                    return
            except (DeploymentError, ValueError):
                pass
            time.sleep(1)
        raise DeploymentError('New Tunnel process did not become healthy within 60 seconds')

    def _history(self, event, **fields):
        entry = {'timestamp': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}
        with (self.control / 'history.jsonl').open('a') as output:
            os.chmod(output.name, 0o600)
            output.write(json.dumps(entry) + '\n')

    def _snapshot(self, probe_revision):
        launcher = self.root / 'launch.sh'
        atomic_write(self.control / 'pending-launch.sh', launcher.read_bytes(), launcher.stat().st_mode & 0o777)
        state_path = self.control / 'state.json'
        snapshot = {
            'state': json.loads(state_path.read_text()) if state_path.exists() else None,
            'target': os.readlink(self.root / 'current') if (self.root / 'current').is_symlink() else None,
            'probe_revision': probe_revision,
        }
        write_json(self.control / 'pending.json', snapshot)

    def _restore(self):
        snapshot = json.loads((self.control / 'pending.json').read_text())
        probe_release, _ = self._manifest(snapshot['probe_revision'])
        if snapshot['target'] is not None:
            self._pointer(snapshot['target'])
        backup = self.control / 'pending-launch.sh'
        atomic_write(self.root / 'launch.sh', backup.read_bytes(), backup.stat().st_mode & 0o777)
        if snapshot['target'] is None:
            self._pointer(None)
        if snapshot['state'] is None:
            (self.control / 'state.json').unlink(missing_ok=True)
        else:
            write_json(self.control / 'state.json', snapshot['state'])
        self._restart()
        self._wait_healthy()
        self._probe(probe_release, launcher=True, legacy=self.status()['active'] == 'legacy')

    def _transition(self, target):
        state = self._check_active()
        if target == state['active']:
            raise DeploymentError('Requested release is already active')
        if target == 'legacy':
            if not (self.control / 'legacy-launch.sh').is_file():
                raise DeploymentError('Legacy launcher backup is missing')
            release, _ = self._manifest(state['active'])
        else:
            release, _ = self._manifest(target)
            self._probe(release)
        backup = self.control / 'legacy-launch.sh'
        if state['active'] == 'legacy' and not backup.exists():
            original = self.root / 'launch.sh'
            atomic_write(backup, original.read_bytes(), original.stat().st_mode & 0o777)
        self._snapshot(state['active'] if state['active'] != 'legacy' else target)
        try:
            if target == 'legacy':
                atomic_write(self.root / 'launch.sh', backup.read_bytes(), backup.stat().st_mode & 0o777)
                self._pointer(None)
            else:
                self._pointer(self.root / 'releases' / target)
                atomic_write(self.root / 'launch.sh', self._launcher(), 0o700)
            self._restart()
            self._wait_healthy()
            self._probe(release, launcher=True, legacy=target == 'legacy')
            write_json(self.control / 'state.json', {'active': target, 'previous': state['active']})
            self._history('activated', active=target, previous=state['active'])
        except Exception:
            try:
                self._restore()
            except Exception:
                raise DeploymentError('Transition and recovery failed; inspect status and run recover') from None
            (self.control / 'pending.json').unlink()
            self._history('activation_failed_restored', active=state['active'])
            raise DeploymentError('Activation failed; previous deployment restored and verified') from None
        (self.control / 'pending.json').unlink()

    def activate(self, revision):
        with self._lock():
            self._no_pending()
            self._transition(revision)

    def rollback(self):
        with self._lock():
            self._no_pending()
            previous = self.status()['previous']
            if previous is None:
                raise DeploymentError('No previous deployment is recorded')
            self._transition(previous)

    def recover(self):
        with self._lock():
            if not self.status()['pending']:
                raise DeploymentError('No pending transition to recover')
            self._restore()
            (self.control / 'pending.json').unlink()
            self._history('recovered', active=self.status()['active'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / '.local/share/obsidian-chatgpt-mcp')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--uv', default='/opt/homebrew/bin/uv')
    parser.add_argument('command', choices=['prepare', 'activate', 'rollback', 'recover', 'status'])
    parser.add_argument('revision', nargs='?')
    args = parser.parse_args()
    app = Deployment(args.root, args.repo, uv=args.uv)
    try:
        if args.command == 'prepare':
            print(json.dumps({'prepared': app.prepare(args.revision or 'HEAD')}))
        elif args.command == 'activate':
            if not args.revision:
                parser.error('activate requires a full prepared commit ID')
            app.activate(args.revision)
            print(json.dumps(app.status()))
        elif args.command == 'status':
            print(json.dumps(app.status()))
        else:
            getattr(app, args.command)()
            print(json.dumps(app.status()))
    except (DeploymentError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, DeploymentError) else 'Deployment filesystem/configuration error; inspect status'
        print(json.dumps({'error': message}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
