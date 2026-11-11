"""Run successive real CI cycles against isolated files and fake external I/O."""
import json
from datetime import datetime, timedelta

import pytest
import check_status as cs
import push_utils


@pytest.fixture
def cycles(tmp_path, monkeypatch):
    now = [datetime(2026, 11, 12, 12, 0)]
    rooms = [{"platform": "bilibili", "id": "123", "name": "A"}]
    for attr, name in [("ROOMS_FILE", "rooms.json"), ("STATE_FILE", "state.json"),
                       ("STATUS_FILE", "status.json"), ("TRACKING_FILE", "tracking.json"),
                       ("HISTORY_FILE", "history.json")]:
        monkeypatch.setattr(cs, attr, str(tmp_path / name))
    (tmp_path / "rooms.json").write_text(json.dumps(rooms), encoding="utf-8")
    monkeypatch.setenv("BLIVE_CONFIG", json.dumps({"push": {"type": "wecom", "webhook": "https://example.invalid"}}))
    monkeypatch.setattr(cs, "bjnow", lambda: now[0])
    observed = [1]
    def fetch(ids):
        if isinstance(observed[0], Exception):
            raise observed[0]
        return {"123": {"live_status": observed[0], "title": "Live", "online": 1}}
    monkeypatch.setattr(cs, "fetch_bilibili_batch", fetch)
    calls, outcomes = [], [False, True]
    def send(*args):
        calls.append(args)
        ok = outcomes.pop(0) if outcomes else True
        if isinstance(ok, Exception):
            raise ok
        return push_utils.SendResult(ok=ok, attempts=1, last_error="" if ok else "network timeout", status_code=None)
    monkeypatch.setattr(cs, "dispatch_event", send)
    def run(minutes=0, status=1):
        now[0] += timedelta(minutes=minutes)
        observed[0] = status
        cs.main()
    def read(name):
        p = tmp_path / (name + ".json")
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return run, calls, outcomes, read, tmp_path, rooms


def test_failed_delivery_retries_next_cycle_then_stops(cycles):
    run, calls, _, read, _, _ = cycles
    run()
    assert read("state") == {"bilibili_123": "live"}
    assert read("tracking")["bilibili_123"].get("pending_notification")
    run(minutes=5)
    assert len(calls) == 2
    assert not read("tracking")["bilibili_123"].get("pending_notification")
    run(minutes=5)
    assert len(calls) == 2


def test_repeated_failure_backs_off_and_dispatch_exception_is_retained(cycles):
    run, calls, outcomes, read, _, _ = cycles
    outcomes[:] = [RuntimeError("timeout"), False, True]
    run()
    run(minutes=1)
    assert len(calls) == 1
    run(minutes=4)
    assert len(calls) == 2
    run(minutes=5)
    assert len(calls) == 2
    run(minutes=5)
    assert len(calls) == 3
    assert not read("tracking")["bilibili_123"].get("pending_notification")


@pytest.mark.parametrize("stop", ["offline", "disabled", "removed"])
def test_pending_notification_is_discarded_when_no_longer_relevant(cycles, stop):
    run, calls, _, read, folder, rooms = cycles
    run()
    assert read("tracking")["bilibili_123"].get("pending_notification")
    if stop == "disabled":
        rooms[0]["enabled"] = False
    if stop == "removed":
        rooms.clear()
    (folder / "rooms.json").write_text(json.dumps(rooms), encoding="utf-8")
    run(minutes=5, status=0 if stop == "offline" else 1)
    assert len(calls) == 1
    assert not read("tracking")["bilibili_123"].get("pending_notification")


def test_pending_retry_respects_silence(cycles):
    run, calls, _, read, folder, _ = cycles
    run()
    path = folder / "silence_state.json"
    path.write_text('{"enabled":true,"start":"12:00","end":"13:00"}', encoding="utf-8")
    run(minutes=5)
    assert len(calls) == 1
    assert read("tracking")["bilibili_123"].get("pending_notification")
    run(minutes=55)
    assert len(calls) == 2


def test_multiple_rooms_only_retry_the_failed_channel(cycles, monkeypatch):
    run, calls, _, _, folder, rooms = cycles
    rooms.append({"platform":"bilibili", "id":"456", "name":"B", "tags":["b"]})
    (folder / "rooms.json").write_text(json.dumps(rooms), encoding="utf-8")
    monkeypatch.setattr(cs, "fetch_bilibili_batch", lambda ids: {rid:{"live_status":1,"title":"Live"} for rid in ids})
    monkeypatch.setenv("BLIVE_CONFIG", json.dumps({"channels":[
        {"id":"a","type":"wecom","fields":{"webhook":"a"}}, {"id":"b","type":"wecom","fields":{"webhook":"b"}}],
        "routes":[{"match":{"tag":"b"},"channelId":"b"},{"match":{},"channelId":"a"}]}))
    # Channel shape follows the existing resolver; capture room identity in title.
    def dispatch(cfg, context, title, body):
        calls.append(title)
        return push_utils.SendResult(ok=("B" in title or len(calls)>2), attempts=1, last_error="timeout", status_code=None)
    monkeypatch.setattr(cs, "dispatch_event", dispatch)
    run()
    run(minutes=5)
    assert len(calls) == 3
    assert "A" in calls[-1] and "B" not in calls[-1]


def test_initial_batch_failure_recovers_as_first_valid_detection(cycles):
    run, calls, outcomes, _, _, _ = cycles
    outcomes[:] = [True]
    run(status=RuntimeError("offline network"))
    assert calls == []
    run(minutes=5)
    assert len(calls) == 1


def test_pending_retry_waits_for_fresh_confirmation(cycles):
    run, calls, _, _, _, _ = cycles
    run()
    run(minutes=5, status=RuntimeError("network"))
    assert len(calls) == 1
    run(minutes=5)
    assert len(calls) == 2


@pytest.mark.parametrize("previous,expected", [("offline", 1), ("live", 1)])
def test_douyin_error_keeps_last_reliable_state_and_live_start(cycles, monkeypatch, previous, expected):
    run, calls, outcomes, read, folder, rooms = cycles
    rooms[0]["platform"] = "douyin"
    (folder / "rooms.json").write_text(json.dumps(rooms), encoding="utf-8")
    states = iter([previous, "error", "live"])
    monkeypatch.setattr(cs, "fetch_douyin", lambda rid: {"status":next(states),"title":"fixture"})
    outcomes[:] = [True, True]
    run()
    first = read("tracking")["douyin_123"].get("live_start")
    run(minutes=5)
    assert read("state")["douyin_123"] == previous
    if previous == "live":
        assert read("tracking")["douyin_123"]["live_start"] == first
    run(minutes=130)  # beyond the dedup cooldown: the state machine must prevent duplication
    assert len(calls) == expected


def test_unrecognized_douyin_page_is_error_not_offline(monkeypatch):
    monkeypatch.setattr(cs, "fetch_with_retry", lambda *a, **k: b'<html>Verification required</html>')
    assert cs.fetch_douyin("123")["status"] == "error"


def test_legacy_error_state_recovers_as_unknown(cycles):
    run,calls,outcomes,_,folder,_=cycles
    (folder/'state.json').write_text('{"bilibili_123":"error"}',encoding='utf-8')
    outcomes[:]=[True]
    run()
    assert len(calls)==1


def test_missing_bilibili_status_does_not_clear_pending_delivery(cycles,monkeypatch):
    run,calls,_,read,_,_=cycles
    run()
    start=read('tracking')['bilibili_123']['live_start']
    monkeypatch.setattr(cs,'fetch_bilibili_batch',lambda ids:{'123':{'title':'incomplete'}})
    run(minutes=5)
    assert read('state')['bilibili_123']=='live'
    assert read('tracking')['bilibili_123']['live_start']==start
    assert read('tracking')['bilibili_123'].get('pending_notification')
    assert len(calls)==1
    monkeypatch.setattr(cs,'fetch_bilibili_batch',lambda ids:{'123':{'live_status':1,'title':'recovered'}})
    run(minutes=5)
    assert len(calls)==2


def test_unrecognized_douyin_status_code_is_not_confirmed_offline(monkeypatch):
    page=r'{\"id_str\":\"123\",\"status\":99,\"title\":\"fixture\",\"user_count\":1}'
    monkeypatch.setattr(cs,'fetch_with_retry',lambda *a,**k:page.encode('utf-8'))
    assert cs.fetch_douyin('123')['status']=='error'
