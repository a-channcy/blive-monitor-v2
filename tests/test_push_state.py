"""Exercise retry exits and data preservation with local Git remotes, no GitHub writes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.parametrize('reject_count,expected', [(0, 0), (1, 0), (10, 1)])
def test_push_exit_and_preserved_state(tmp_path, reject_count, expected):
    remote = tmp_path / 'remote.git'
    local = tmp_path / 'local'
    git(tmp_path, 'init', '--bare', str(remote))
    git(tmp_path, 'clone', str(remote), str(local))
    git(local, 'checkout', '-b', 'main')
    git(local, 'config', 'user.email', 'test@example.invalid')
    git(local, 'config', 'user.name', 'Test')
    for name in ['merge_state.py', 'common.py', 'log_utils.py']:
        shutil.copyfile(ROOT / name, local / name)
    (local / 'status.json').write_text('{"updated":"old","rooms":[]}', encoding='utf-8')
    git(local, 'add', '.')
    git(local, 'commit', '-m', 'base')
    git(local, 'push', 'origin', 'HEAD:main')
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\nn=0\n[ ! -f attempts ] || n=$(cat attempts)\nn=$((n+1))\necho "$n" > attempts\n[ "$n" -gt '+str(reject_count)+' ]\n', encoding='utf-8')
    hook.chmod(0o755)
    newest = '{"updated":"new","rooms":[]}'
    (local / 'status.json').write_text(newest, encoding='utf-8')
    git(local, 'add', 'status.json')
    git(local, 'commit', '-m', 'new state')
    script = ROOT / 'tools' / 'push_state.py'
    assert script.exists(), 'bounded push helper has not been implemented'
    result = subprocess.run([sys.executable, str(script)], cwd=local, capture_output=True,
                            text=True, encoding='utf-8', env={**os.environ, 'PYTHONUTF8':'1'})
    assert result.returncode == expected, result.stdout + result.stderr
    assert (local / 'status.json').read_text(encoding='utf-8') == newest
    attempts = int((remote / 'attempts').read_text())
    assert attempts == min(reject_count + 1, 2)
    saved = git(remote, 'show', 'main:status.json').stdout.decode()
    if expected == 0:
        assert saved == newest
    else:
        assert 'old' in saved
        assert '::error::' in result.stderr
