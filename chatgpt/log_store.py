"""Bounded JSON log storage for concurrent gateway processes on macOS/POSIX."""

import fcntl
import os
from pathlib import Path


class BoundedLog:
    def __init__(self, directory, max_bytes=10 * 1024 * 1024, backups=5):
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.backups = backups

    def write(self, line):
        data = line.encode('utf-8')
        if len(data) > self.max_bytes:
            raise ValueError('Diagnostic record exceeds log size limit')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW
        with os.fdopen(os.open(self.directory / 'gateway.lock', flags, 0o600), 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self.directory / 'gateway.jsonl'
            if current.exists() and current.stat().st_size + len(data) > self.max_bytes:
                for i in range(self.backups, 0, -1):
                    source = current if i == 1 else self.directory / f'gateway.jsonl.{i - 1}'
                    if source.exists():
                        os.replace(source, self.directory / f'gateway.jsonl.{i}')
            # Reopen after locking: another process may have rotated the inode.
            with os.fdopen(os.open(current, flags | os.O_APPEND, 0o600), 'ab') as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(data)
