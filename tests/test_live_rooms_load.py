"""Regression: unauthenticated rooms load resolves to an array using public data.

The actual loader is exercised in Node, including error/stale-data behavior.
"""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required for frontend regression')
def test_no_token_load_resolves_rooms_and_handles_errors():
    result = subprocess.run(
        ['node', '--test', 'tests/frontend_data.test.cjs'], cwd=ROOT,
        capture_output=True, text=True, encoding='utf-8',
    )
    assert result.returncode == 0, result.stdout + result.stderr
