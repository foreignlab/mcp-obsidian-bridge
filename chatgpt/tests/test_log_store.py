import json
import os
from pathlib import Path
import subprocess
import sys


def test_rotates_only_owned_logs_and_bounds_size(tmp_path):
    from log_store import BoundedLog

    (tmp_path / 'unrelated.log').write_text('keep')
    store = BoundedLog(tmp_path, max_bytes=20, backups=2)
    for i in range(10):
        store.write(json.dumps({'n': i}) + '\n')
    files = sorted(tmp_path.glob('gateway.jsonl*'))
    assert len(files) == 3
    assert all(f.stat().st_size <= 20 for f in files)
    assert (tmp_path / 'unrelated.log').read_text() == 'keep'
    assert json.loads((tmp_path / 'gateway.jsonl').read_text().splitlines()[-1]) == {'n': 9}
    assert all(f.stat().st_mode & 0o777 == 0o600 for f in files)


def test_diagnostics_can_use_bounded_file_without_stderr(monkeypatch, tmp_path, capsys):
    from diagnostics import Diagnostics

    monkeypatch.setenv('OBSIDIAN_DIAGNOSTICS_DIR', str(tmp_path / 'logs'))
    Diagnostics().emit('gateway_starting')
    assert capsys.readouterr().err == ''
    result = json.loads((tmp_path / 'logs/gateway.jsonl').read_text())
    assert result['event'] == 'gateway_starting'
    assert (tmp_path / 'logs').stat().st_mode & 0o777 == 0o700


def test_unavailable_store_falls_back_to_stderr(monkeypatch, tmp_path, capsys):
    from diagnostics import Diagnostics

    target = tmp_path / 'not-a-directory'
    target.write_text('keep')
    monkeypatch.setenv('OBSIDIAN_DIAGNOSTICS_DIR', str(target))
    Diagnostics().emit('gateway_starting')
    assert json.loads(capsys.readouterr().err)['event'] == 'gateway_starting'
    assert target.read_text() == 'keep'


def test_concurrent_processes_do_not_lose_or_interleave_records(tmp_path):
    code = '''
import sys, json
from pathlib import Path
from log_store import BoundedLog
store = BoundedLog(Path(sys.argv[1]), max_bytes=1024, backups=20)
for i in range(40):
    store.write(json.dumps({'worker': sys.argv[2], 'i': i}) + '\\n')
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    workers = [subprocess.Popen([sys.executable, '-c', code, str(tmp_path), str(i)], env=env) for i in range(4)]
    for worker in workers:
        assert worker.wait(timeout=10) == 0
    records = [json.loads(line) for path in tmp_path.glob('gateway.jsonl*') for line in path.read_text().splitlines()]
    assert len(records) == 160
    assert len({(r['worker'], r['i']) for r in records}) == 160
