"""A standalone silence change must not require replacing push credentials."""
import json
from datetime import datetime

import pytest
import common
import check_status as cs


def test_saved_silence_takes_precedence_without_changing_push(tmp_path, monkeypatch):
    rooms = tmp_path / "rooms.json"
    rooms.write_text("[]", encoding="utf-8")
    mirror = tmp_path / "silence_state.json"
    mirror.write_text(json.dumps({"enabled": True, "start": "23:00", "end": "08:00"}), encoding="utf-8")
    monkeypatch.setattr(cs, "ROOMS_FILE", str(rooms))
    monkeypatch.setattr(cs, "SILENCE_FILE", str(mirror), raising=False)
    original = {"push": {"type": "wecom", "webhook": "https://example.invalid"},
                "silence": {"enabled": False}, "summary": {"enabled": True}}
    monkeypatch.setenv("BLIVE_CONFIG", json.dumps(original))
    cfg = cs.load_config()
    assert common.should_skip_by_silence(datetime(2026, 11, 12, 23, 30), cfg["silence"])
    assert cfg["cfg_all"]["push"] == original["push"]
    assert cfg["cfg_all"]["summary"] == original["summary"]


def test_missing_silence_file_uses_legacy_secret(tmp_path):
    legacy = {"enabled": True, "start": "23:00", "end": "08:00"}
    assert common.load_silence_cfg(json.dumps({"silence": legacy}), str(tmp_path / "missing.json")) == legacy


@pytest.mark.parametrize("contents", ['{"enabled":true}', '{broken', '[]', '{"enabled":true,"start":"99:00","end":"08:00"}'])
def test_invalid_silence_file_cannot_silently_disable_silence(tmp_path, contents):
    path = tmp_path / "silence_state.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match="silence_state"):
        common.load_silence_cfg("{}", str(path))


def test_explicit_disable_overrides_legacy_silence(tmp_path):
    path = tmp_path / "silence_state.json"
    path.write_text('{"enabled":false,"start":"23:00","end":"08:00"}', encoding="utf-8")
    assert common.load_silence_cfg('{"silence":{"enabled":true}}', str(path))["enabled"] is False
