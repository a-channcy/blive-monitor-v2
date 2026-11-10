"""Push the CI state commit; fail explicitly when bounded retries are exhausted.

Only run in the disposable Actions checkout after the initial state commit.
The retry keeps the existing semantic merge policy and preserves generated covers.
"""
from pathlib import Path
import subprocess
import sys

STATE_FILES = (
    'state.json', 'tracking.json', 'history.json', 'post_tracking.json',
    'post_rooms.json', 'notify_dedup.json', 'status.json', 'summary_state.json',
    '.keepalive',
)


def run(*args):
    return subprocess.run(args, check=True)


def push_state():
    for attempt in range(1, 3):
        if subprocess.run(['git', 'push', 'origin', 'HEAD:main']).returncode == 0:
            print('State push succeeded.')
            return 0
        if attempt == 2:
            print('::error::State push failed after 2 attempts; latest state was not saved remotely.', file=sys.stderr)
            return 1
        print('State push failed; fetch and semantically merge before retrying.')
        run('git', 'fetch', 'origin', 'main')
        run(sys.executable, 'merge_state.py', 'origin/main')
        paths = [Path(name) for name in STATE_FILES]
        paths += [p for p in Path('assets/covers').rglob('*') if p.is_file()]
        saved = {p: p.read_bytes() for p in paths if p.is_file()}
        run('git', 'reset', '--hard', 'origin/main')
        for path, content in saved.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        if saved:
            run('git', 'add', '-f', '--', *map(str, saved))
        diff = subprocess.run(['git', 'diff', '--cached', '--quiet']).returncode
        if diff == 1:
            run('git', 'commit', '-m', '📡 update state [skip ci]')
        elif diff != 0:
            raise RuntimeError('Unable to inspect staged state')
    return 1


if __name__ == '__main__':
    try:
        sys.exit(push_state())
    except (subprocess.CalledProcessError, OSError, RuntimeError) as error:
        print(f'::error::State retry stopped: {error}', file=sys.stderr)
        sys.exit(1)
