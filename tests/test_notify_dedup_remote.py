"""Real ledger files and HTTP request handling against an in-memory Contents API."""
import base64
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import notify_dedup as nd
from merge_state import merge_notify_dedup
from test_live_delivery import cycles


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'test-token')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'example/monitor')
    monkeypatch.setenv('DEDUP_BRANCH', 'main')
    state = {'ledger': {}, 'sha': 'initial', 'requests': [], 'conflicts': 0,
             'failure': None, 'missing': False}

    def urlopen(req, timeout):
        assert timeout <= 15
        assert req.get_header('Authorization') == 'Bearer test-token'
        parsed = urllib.parse.urlparse(req.full_url)
        assert parsed.scheme == 'https' and parsed.netloc == 'api.github.com'
        assert parsed.path == '/repos/example/monitor/contents/notify_dedup.json'
        method = req.get_method()
        state['requests'].append(method)
        if state['failure']:
            raise state['failure']
        if method == 'GET':
            assert urllib.parse.parse_qs(parsed.query) == {'ref': ['main']}
            if state['missing']:
                raise urllib.error.HTTPError(req.full_url, 404, 'missing', {}, None)
            payload = {'sha': state['sha'], 'encoding': 'base64', 'content': base64.b64encode(
                json.dumps(state['ledger']).encode()).decode()}
        else:
            assert method == 'PUT'
            body = json.loads(req.data)
            assert body['branch'] == 'main'
            assert '[skip ci]' in body['message']
            if state['missing']:
                assert 'sha' not in body
            else:
                assert body['sha'] == state['sha']
            if state['conflicts']:
                state['conflicts'] -= 1
                state['sha'] += '-concurrent'
                state['ledger']['post:concurrent:1'] = {'ts': 50}
                raise urllib.error.HTTPError(req.full_url, 409, 'conflict', {}, None)
            state['ledger'] = json.loads(base64.b64decode(body['content']))
            state['missing'] = False
            state['sha'] += '-saved'
            payload = {'content': {'sha': state['sha']}}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    return state


def test_success_is_remotely_saved_before_state_persist(remote):
    nd.record('live:bilibili_123', now=200)
    assert remote['ledger']['live:bilibili_123']['ts'] == 200
    assert remote['requests'] == ['GET', 'PUT']


def test_sync_restores_remote_success_and_unifies_persist_policy(remote):
    remote['ledger'] = {'live:one': {'ts': 200}, 'post:author:1': {'ts': 90}}
    nd._save({'live:one': {'ts': 100}, 'post:author:1': {'ts': 100}})
    assert nd.sync_from_remote() == 2
    assert not nd.should_notify('live:one', cooldown=150, now=300)
    merged = merge_notify_dedup(nd._load(), {'live:one': {'ts': 100}})
    assert merged['live:one']['ts'] == 200
    assert merged['post:author:1']['ts'] == 90


def test_conflict_reloads_and_preserves_concurrent_records(remote):
    remote['conflicts'] = 1
    nd.record('live:new', now=200)
    assert remote['ledger'] == {'live:new': {'ts': 200}, 'post:concurrent:1': {'ts': 50}}
    assert remote['requests'] == ['GET', 'PUT', 'GET', 'PUT']


def test_missing_remote_ledger_can_be_created(remote):
    remote['missing'] = True
    assert nd.sync_from_remote() == 0
    nd.record('post:author:1', now=100)
    assert remote['ledger'] == {'post:author:1': {'ts': 100}}


@pytest.mark.parametrize('failure', [
    urllib.error.URLError('offline'),
    urllib.error.HTTPError('https://api.github.com', 403, 'forbidden', {}, None),
])
def test_failure_preserves_local_success_and_warns(remote, caplog, failure):
    remote['failure'] = failure
    nd.record('live:one', now=200)
    assert not nd.should_notify('live:one', now=201)
    assert remote['ledger'] == {}
    assert caplog.records
    assert 'test-token' not in caplog.text


def test_conflict_retry_is_bounded(remote, caplog):
    remote['conflicts'] = 5
    nd.record('live:one', now=200)
    assert remote['requests'] == ['GET', 'PUT', 'GET', 'PUT']
    assert not nd.should_notify('live:one', now=201)
    assert caplog.records


def test_no_credentials_never_attempts_network(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Network must not be used without credentials')
    monkeypatch.setattr(urllib.request, 'urlopen', unexpected)
    nd.record('live:one', now=200)
    assert nd.sync_from_remote() == 0
    assert not nd.push_to_remote()
    assert not nd.should_notify('live:one', now=201)


def test_remote_writes_can_be_disabled(remote, monkeypatch):
    monkeypatch.setenv('DEDUP_PUSH_REMOTE', '0')
    nd.record('live:one', now=200)
    assert remote['requests'] == []
    assert not nd.should_notify('live:one', now=201)


def test_invalid_remote_document_is_not_overwritten(remote, caplog):
    remote['ledger'] = ['unexpected format']
    nd.record('live:one', now=200)
    assert remote['requests'] == ['GET']
    assert remote['ledger'] == ['unexpected format']
    assert not nd.should_notify('live:one', now=201)
    assert caplog.records


def test_corrupt_entries_do_not_destroy_valid_remote_or_local_records(remote):
    remote['ledger'] = {'live:one': {'ts': 'nan'}, 'live:two': {'ts': 300},
                        'post:author:1': None}
    nd._save({'live:one': {'ts': 200}})
    nd.sync_from_remote()
    assert not nd.should_notify('live:one', now=201)
    assert nd._load()['live:two']['ts'] == 300
    assert not nd.should_notify('post:author:1', cooldown=nd.PERMANENT, now=1000)


def test_live_detection_syncs_before_deciding_to_send(remote, cycles):
    remote['ledger'] = {'live:bilibili_123': {'ts': time.time()}}
    run, calls, _, read, _, _ = cycles
    run()
    assert calls == []
    assert not read('tracking')['bilibili_123'].get('pending_notification')
