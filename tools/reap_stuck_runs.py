#!/usr/bin/env python3
"""Cancel stale unstarted monitor runs, independently of monitor and Pages.

Running jobs are governed by their job timeout. List by status and branch,
then recheck each candidate before cancellation. Never force-cancel a run.
Set REAP_DRY_RUN=1 for read-only inspection.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

STUCK_STATUSES = ('waiting', 'queued', 'pending', 'requested')
PER_PAGE = 100
MAX_PAGES = 10  # GitHub filtered run queries expose up to 1,000 results.


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def find_stuck(runs, now, stuck_minutes=20):
    """Use the latest run timestamp so an old but freshly rerun job is safe."""
    found = []
    for run in runs or []:
        if not isinstance(run, dict) or run.get('status') not in STUCK_STATUSES:
            continue
        created = _parse_ts(run.get('created_at'))
        if created is None:
            continue
        times = [created]
        for field in ('updated_at', 'run_started_at'):
            timestamp = _parse_ts(run.get(field))
            if timestamp is not None:
                times.append(timestamp)
        if (now - max(times)).total_seconds() >= stuck_minutes * 60:
            found.append(run)
    return found


def api(method, path, token, body=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = urllib.request.Request(
        'https://api.github.com' + path, data=data, method=method,
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                 'User-Agent': 'blive-monitor-reaper', 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (OSError, ValueError) as exc:
        print(f'::warning::Reaper API failed ({type(exc).__name__}).')
        return 0, None


def _in_scope(run, workflow, branch):
    return (isinstance(run, dict) and run.get('head_branch') == branch
            and str(run.get('path', '')).split('@')[0] == f'.github/workflows/{workflow}')


def main():
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY')
    if not token or not repo:
        print('Reaper skipped: GITHUB_TOKEN and GITHUB_REPOSITORY are required.')
        return 0
    workflow = os.environ.get('REAP_TARGET_WORKFLOW', 'check.yml')
    branch = os.environ.get('REAP_TARGET_BRANCH', 'main')
    dry_run = os.environ.get('REAP_DRY_RUN', '0').lower() in ('1', 'true')
    try:
        threshold = int(os.environ.get('REAP_STUCK_MINUTES', '20'))
        max_cancel = int(os.environ.get('REAP_MAX_CANCEL', '5'))
        if threshold <= 0 or max_cancel <= 0:
            raise ValueError('positive values required')
    except ValueError:
        print('::error::Invalid reaper threshold or cancellation limit.')
        return 1

    candidates = {}
    endpoint = f'/repos/{repo}/actions/workflows/{urllib.parse.quote(workflow, safe="")}/runs'
    # Finish listing before cancelling so removals cannot shift pagination.
    for status in STUCK_STATUSES:
        for page in range(1, MAX_PAGES + 1):
            query = urllib.parse.urlencode({'status': status, 'branch': branch,
                                            'per_page': PER_PAGE, 'page': page})
            code, payload = api('GET', endpoint + '?' + query, token)
            if code != 200 or not isinstance(payload, dict) or not isinstance(payload.get('workflow_runs'), list):
                print(f'::error::Unable to list {status} runs (HTTP {code}); no runs cancelled.')
                return 1
            runs = payload['workflow_runs']
            for run in find_stuck(runs, _now(), threshold):
                run_id = run.get('id')
                if (isinstance(run_id, int) and _in_scope(run, workflow, branch)
                        and str(run_id) != os.environ.get('GITHUB_RUN_ID')):
                    candidates[run_id] = run
            if len(runs) < PER_PAGE:
                break
        else:
            print('::error::Reaper pagination limit reached; no runs cancelled.')
            return 1

    failed = False
    cancelled = 0
    ordered = sorted(candidates.values(), key=lambda run: _parse_ts(run['created_at']))
    for run in ordered[:max_cancel]:
        run_id = run['id']
        path = f'/repos/{repo}/actions/runs/{run_id}'
        code, current = api('GET', path, token)
        if code != 200:
            print(f'::warning::Unable to recheck run {run_id} (HTTP {code}); skipped.')
            failed = True
            continue
        if not _in_scope(current, workflow, branch) or not find_stuck([current], _now(), threshold):
            continue
        if dry_run:
            print(f'Dry run: would cancel {run_id} ({current["status"]}).')
            continue
        code, _ = api('POST', path + '/cancel', token, {})
        if code in (202, 204):
            cancelled += 1
        else:
            print(f'::warning::Cancellation failed for {run_id} (HTTP {code}); no force-cancel.')
            failed = True
    print(f'Reaper: {len(candidates)} candidates; {cancelled} cancellation requests accepted.')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
