import json
from datetime import datetime

import pytest
import auto_summary as summary
import push_utils
import merge_state


@pytest.fixture
def summary_cycles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime(2026, 11, 12, 12)
    clock = [int(now.replace(tzinfo=summary.common.BEIJING_TZ).timestamp())]
    monkeypatch.setattr(summary, "bjnow", lambda: now)
    monkeypatch.setattr(summary.time, "time", lambda: clock[0])
    monkeypatch.setenv("BLIVE_CONFIG", json.dumps({"push":{"type":"wecom","webhook":"https://example.invalid"},
        "summary":{"enabled":False,"freq":"daily","sendTime":"23:00"}}))
    path=tmp_path / "summary_state.json"
    path.write_text(json.dumps({"enabled":True,"freq":"daily","sendTime":"23:00","requestId":"req-1","requestedAt":clock[0]}),encoding="utf-8")
    calls, outcomes=[],[True]
    def dispatch(*args):
        calls.append(args)
        result=outcomes.pop(0) if outcomes else True
        if isinstance(result, Exception):
            raise result
        return push_utils.SendResult(ok=result,attempts=1,last_error="" if result else "timeout",status_code=None)
    monkeypatch.setattr(push_utils,"dispatch_event",dispatch)
    def run():
        with pytest.raises(SystemExit) as exc:
            summary.main()
        assert exc.value.code == 0
    def state():
        return json.loads(path.read_text(encoding="utf-8"))
    return run, state, path, calls, outcomes, clock


def test_manual_request_is_delivered_then_acknowledged_once(summary_cycles):
    run,state,path,calls,_,_=summary_cycles
    run()
    assert len(calls)==1
    assert state()["lastHandledRequestId"]=="req-1"
    assert state()["lastSent"]>0
    run()
    assert len(calls)==1


@pytest.mark.parametrize("failure", [False, RuntimeError("network failure")])
def test_manual_failure_keeps_request_and_cooldown_before_retry(summary_cycles, failure):
    run,state,_,calls,outcomes,clock=summary_cycles
    outcomes[:]=[failure,True]
    run()
    assert len(calls)==1
    assert not state().get("lastSent")
    assert state().get("lastHandledRequestId")!="req-1"
    run()
    assert len(calls)==1
    clock[0]+=summary.DEFAULT_COOLDOWN_SECONDS
    run()
    assert len(calls)==2
    assert state()["lastHandledRequestId"]=="req-1"


def test_manual_request_respects_silence(summary_cycles, tmp_path):
    run,state,_,calls,_,_=summary_cycles
    silence=tmp_path / "silence_state.json"
    silence.write_text('{"enabled":true,"start":"11:00","end":"13:00"}',encoding="utf-8")
    run()
    assert calls==[]
    assert state().get("lastHandledRequestId")!="req-1"
    silence.write_text('{"enabled":false,"start":"11:00","end":"13:00"}',encoding="utf-8")
    run()
    assert len(calls)==1


@pytest.mark.parametrize("freq",["daily","weekly"])
def test_file_settings_enable_scheduled_summary_without_secret_rewrite(summary_cycles,freq):
    run,state,path,calls,_,_=summary_cycles
    path.write_text(json.dumps({"enabled":True,"freq":freq,"sendTime":"09:00"}),encoding="utf-8")
    run()
    assert len(calls)==1
    assert state()["freq"]==freq
    run()
    assert len(calls)==1


def test_state_merge_keeps_new_remote_request_and_local_delivery_receipt():
    local={"enabled":True,"freq":"daily","requestId":"old","requestedAt":10,
           "lastHandledRequestId":"old","lastSent":30}
    remote={"enabled":False,"freq":"weekly","requestId":"new","requestedAt":20,"lastSent":5}
    result=merge_state.merge_summary_state(local,remote)
    assert result["requestId"]=="new"
    assert result["lastHandledRequestId"]=="old"
    assert result["lastSent"]==30
    assert result["enabled"] is False
    assert result["freq"]=="weekly"


def test_state_merge_does_not_resurrect_cooldown_cleared_by_success():
    local={"lastSent":30,"lastHandledRequestId":"old"}
    remote={"requestId":"old","lastSent":5,"lastFailedAt":15,"lastFailedSince":0}
    merged=merge_state.merge_summary_state(local,remote)
    assert not merged.get("lastFailedAt")
    assert merged["requestId"]==merged["lastHandledRequestId"]


def test_ci_merge_entrypoint_persists_remote_request_and_local_ack(tmp_path, monkeypatch):
    local={"requestId":"old","lastHandledRequestId":"old","lastSent":30}
    remote={"enabled":False,"requestId":"new","requestedAt":40,"lastSent":5}
    path=tmp_path/'summary_state.json'
    path.write_text(json.dumps(local),encoding='utf-8')
    monkeypatch.setattr(merge_state,'git_show',lambda repo,ref,name: json.dumps(remote) if name=='summary_state.json' else None)
    monkeypatch.setattr(merge_state.sys,'argv',['merge_state.py','origin/main','--repo',str(tmp_path)])
    assert merge_state.main()==0
    merged=json.loads(path.read_text(encoding='utf-8'))
    assert merged['requestId']=='new'
    assert merged['lastHandledRequestId']=='old'
    assert merged['lastSent']==30


def test_shared_summary_gate_backend_acknowledges_manual_request(summary_cycles):
    _,_,_,calls,_,_=summary_cycles
    class MemoryPersist:
        def __init__(self):
            self.state={"requestId":"backend-request"}
        def get_summary_state(self):
            return dict(self.state)
        def set_summary_state(self,data,remove=None):
            self.state.update(data)
            for key in remove or []:
                self.state.pop(key,None)
        def get_events(self):
            return []
    persist=MemoryPersist()
    cfg={"summary":{"enabled":False},"push":{"type":"wecom","webhook":"test"}}
    summary.run_summary(cfg_all=cfg,persist=persist,now=datetime(2026,11,12,12))
    summary.run_summary(cfg_all=cfg,persist=persist,now=datetime(2026,11,12,12,5))
    assert len(calls)==1
    assert persist.state['lastHandledRequestId']=='backend-request'
