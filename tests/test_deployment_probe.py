from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest


def load_probe():
    spec = importlib.util.spec_from_file_location('probe', Path(__file__).parents[1] / 'scripts/probe_gateway.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_accepts_recent_metadata_and_empty_vault():
    probe = load_probe()
    assert probe.validate_recent([]) == 0
    rows = [{'filename': 'test.md', 'result': {'file.mtime': datetime.now(timezone.utc).isoformat()}}]
    assert probe.validate_recent(rows) == 1


@pytest.mark.parametrize('rows', [
    {}, [{'filename': 'test.md', 'result': {'file.mtime': 'invalid'}}],
    [{'filename': 'test.md', 'result': {'file.mtime': '2020-01-01T00:00:00+00:00'}}],
    [{'filename': 'test.md', 'result': {'file.mtime': '2026-09-22T00:00:00'}}],
    [{'filename': 'test.md', 'result': {'file.mtime': 'invalid'}}] * 4,
])
def test_probe_rejects_invalid_recent_results_without_echoing_them(rows):
    probe = load_probe()
    with pytest.raises(ValueError) as error:
        probe.validate_recent(rows)
    assert 'test.md' not in str(error.value)
