"""Private client settings for shared-server deployment and read-only probes."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tomllib


class SharedRuntimeError(Exception):
    """A fixed, privacy-safe operator error."""


@dataclass
class ClientProfile:
    command: str
    args: list[str]
    env: dict[str, str] = field(repr=False)
    cwd: Path | None = None


def reserved(key: str) -> bool:
    return key.startswith(('UV_', 'PYTHON')) or key in ('VIRTUAL_ENV', '__PYVENV_LAUNCHER__')


def clean_environment(connection: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not reserved(key) and not key.startswith('OBSIDIAN_')
           and key not in ('REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
    env.update({key: value for key, value in connection.items() if not reserved(key)})
    return env


def read_client_profile(path: Path, server: str) -> ClientProfile:
    try:
        text = path.read_text()
        if path.suffix.lower() == '.toml':
            entry = tomllib.loads(text)['mcp_servers'][server]
        else:
            entry = json.loads(text)['mcpServers'][server]
        command = entry['command']
        args = entry.get('args', [])
        env = entry['env']
        cwd = entry.get('cwd')
        if (not isinstance(command, str) or not command or '\x00' in command
                or not isinstance(args, list) or any(not isinstance(a, str) or '\x00' in a for a in args)
                or not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                    or '\x00' in k + v or '=' in k for k, v in env.items())
                or (cwd is not None and (not isinstance(cwd, str) or not Path(cwd).is_absolute()))):
            raise ValueError()
        required = ('OBSIDIAN_API_KEY', 'OBSIDIAN_HOST', 'OBSIDIAN_PORT',
                    'OBSIDIAN_PROTOCOL', 'REQUESTS_CA_BUNDLE')
        if any(not env.get(key) for key in required) or env['OBSIDIAN_PROTOCOL'] != 'https':
            raise ValueError()
        if not 1 <= int(env['OBSIDIAN_PORT']) <= 65535:
            raise ValueError()
        ca = Path(env['REQUESTS_CA_BUNDLE'])
        if not ca.is_absolute() or not ca.is_file() or not os.access(ca, os.R_OK):
            raise ValueError()
        return ClientProfile(command, list(args), dict(env), Path(cwd) if cwd else None)
    except (OSError, ValueError, TypeError, KeyError):
        raise SharedRuntimeError('Invalid private client configuration or HTTPS settings') from None
