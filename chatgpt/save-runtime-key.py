"""Save the OpenAI tunnel runtime key without terminal echo or shell history."""

import getpass
import os
from pathlib import Path
import sys


def main():
    if not sys.stdin.isatty():
        raise SystemExit('Run this command directly in an interactive terminal.')
    path = Path(__file__).resolve().parent / 'tunnel/runtime-api-key'
    if path.exists():
        raise SystemExit('A key is already saved. No changes were made.')
    key = getpass.getpass('OpenAI runtime API key (input hidden): ').strip()
    if not key.startswith('sk-') or any(char.isspace() for char in key):
        raise SystemExit('The key format was not recognized. No key was saved.')
    path.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(key + '\n')
    print('Key saved privately. The tunnel has not been started yet.')


if __name__ == '__main__':
    main()
