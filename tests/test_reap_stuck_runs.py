"""Exercise selection, pagination and cancellation races without GitHub writes."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from tools import reap_stuck_runs as reaper

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def run_record(run_id=1, status='queued', **kwargs):
    return {'id': run_id, 'status': status, 'created_at': '2026-11-12T10:00:00Z',
            'updated_at': '2026-11-12T10:00:00Z', 'run_started_at': '2026-11-12T10:00:00Z',
            'head_branch': 'main', 'path': '.github/workflows/check.yml', **kwargs}


@pytest.mark.parametrize('status,expected', [
    ('waiting', True), ('queued', True), ('pending', True), ('requested', True),
    ('in_progress', False), ('completed', False),
])
def test_only_stale_unstarted_runs_are_selected(status, expected):
    assert bool(reaper.find_stuck([run_record(status=status)], NOW, 20)) is expected


def test_fresh_rerun_future_and_invalid_timestamps_are_not_cancelled():
    records = [run_record(updated_at='2026-11-12T11:50:00Z'),
               run_record(run_started_at='2026-11-12T11:50:00Z'),
               run_record(created_at='invalid'),
               run_record(created_at='2026-11-13T10:00:00Z'), None]
    assert reaper.find_stuck(records, NOW, 20) == []


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'test-token')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'example/monitor')
    monkeypatch.setenv('REAP_TARGET_WORKFLOW', 'check.yml')
    monkeypatch.setenv('REAP_TARGET_BRANCH', 'main')
    monkeypatch.setenv('REAP_DRY_RUN', '0')
    monkeypatch.setattr(reaper, '_now', lambda: NOW)
    state = {'pages': {}, 'current': {}, 'cancelled': [], 'requests': [], 'fail_get': False,
             'cancel_code': 202}

    def fake_api(method, path, token, body=None):
        assert token == 'test-token'
        state['requests'].append((method, path))
        if method == 'GET' and state['fail_get']:
            return 403, None
        parsed = urlparse(path)
        if '/workflows/' in parsed.path:
            assert parsed.path == '/repos/example/monitor/actions/workflows/check.yml/runs'
            query = parse_qs(parsed.query)
            assert query['branch'] == ['main']
            assert query['status'][0] in ('waiting', 'queued', 'pending', 'requested')
            page = int(query['page'][0])
            assert query['per_page'] == ['100']
            records = state['pages'].get((query['status'][0], page), [])
            return 200, {'workflow_runs': records}
        run_id = int(parsed.path.split('/runs/')[1].split('/')[0])
        if method == 'GET':
            return 200, state['current'].get(run_id, run_record(run_id))
        assert method == 'POST' and path.endswith('/cancel')
        state['cancelled'].append(run_id)
        return state['cancel_code'], None

    monkeypatch.setattr(reaper, 'api', fake_api)
    return state


def test_paginates_filtered_runs_and_finds_old_item_beyond_first_page(api):
    api['pages'][('queued', 1)] = [run_record(i, created_at='2026-11-12T11:55:00Z') for i in range(100)]
    api['pages'][('queued', 2)] = [run_record(101)]
    assert reaper.main() == 0
    assert api['cancelled'] == [101]


def test_run_that_started_after_listing_is_not_cancelled(api):
    api['pages'][('waiting', 1)] = [run_record()]
    api['current'][1] = run_record(status='in_progress')
    assert reaper.main() == 0
    assert api['cancelled'] == []


def test_wrong_branch_or_workflow_never_cancelled(api):
    api['pages'][('queued', 1)] = [run_record(1, head_branch='feature'),
                                   run_record(2, path='.github/workflows/pages.yml')]
    assert reaper.main() == 0
    assert api['cancelled'] == []


def test_cancellation_is_bounded_and_duplicate_results_are_deduplicated(api, monkeypatch):
    monkeypatch.setenv('REAP_MAX_CANCEL', '2')
    api['pages'][('queued', 1)] = [run_record(i) for i in range(1, 5)]
    api['pages'][('waiting', 1)] = [run_record(1)]
    assert reaper.main() == 0
    assert api['cancelled'] == [1, 2]


def test_dry_run_never_cancels(api, monkeypatch):
    monkeypatch.setenv('REAP_DRY_RUN', '1')
    api['pages'][('queued', 1)] = [run_record()]
    assert reaper.main() == 0
    assert api['cancelled'] == []


def test_failed_listing_is_visible_and_never_cancels(api):
    api['fail_get'] = True
    assert reaper.main() == 1
    assert api['cancelled'] == []


def test_cancel_failure_is_visible_without_force_cancel(api):
    api['pages'][('queued', 1)] = [run_record()]
    api['cancel_code'] = 403
    assert reaper.main() == 1
    assert len(api['cancelled']) == 1


def test_no_token_or_repo_is_safe(api, monkeypatch):
    monkeypatch.delenv('GITHUB_TOKEN')
    assert reaper.main() == 0
    assert api['requests'] == []


def test_invalid_threshold_fails_before_network(api, monkeypatch):
    monkeypatch.setenv('REAP_STUCK_MINUTES', '-1')
    assert reaper.main() == 1
    assert api['requests'] == []
