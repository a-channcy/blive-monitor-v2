"""Configuration contracts: runtime changes cannot queue a Pages deployment."""
from fnmatch import fnmatchcase
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    # BaseLoader preserves GitHub's `on` key instead of treating it as YAML 1.1 true.
    return yaml.load((ROOT / '.github/workflows' / name).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


def test_pages_only_runs_for_site_changes():
    pages = workflow('pages.yml')
    paths = pages['on']['push']['paths']
    for name in ['state.json','status.json','history.json','rooms.json','summary_state.json','silence_state.json','.keepalive']:
        assert not any(fnmatchcase(name,p) for p in paths), name
    for name in ['monitor.html','index.html','libsodium.js']:
        assert any(fnmatchcase(name,p) for p in paths), name


def test_monitor_has_no_pages_or_test_dependency():
    check, pages = workflow('check.yml'), workflow('pages.yml')
    assert set(check['jobs']) == {'check'}
    assert 'needs' not in check['jobs']['check']
    assert check['concurrency']['group'] != pages['concurrency']['group']
    assert check['concurrency']['cancel-in-progress'] == 'false'
    uses = [s.get('uses','') for s in check['jobs']['check']['steps']]
    assert not any('pages' in action for action in uses)
    assert 'pages' not in check['permissions']


def test_tests_are_not_scheduled_with_monitor():
    tests = workflow('tests.yml')
    assert 'schedule' not in tests['on']
    assert 'pull_request' in tests['on']
    assert 'push' in tests['on']
