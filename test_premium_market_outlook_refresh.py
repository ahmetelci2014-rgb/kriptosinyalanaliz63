from __future__ import annotations

import json

import market_outlook_engine as outlook
import premium_market_outlook_refresh as refresh


def _write(path, ts):
    path.write_text(json.dumps({"updated_at": ts, "snapshots": [{"ts": ts}]}), encoding="utf-8")


def test_fresh_snapshot_does_not_refresh(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    _write(state, 990)

    def fail_run(*args, **kwargs):
        raise AssertionError("fresh state must not refresh")

    monkeypatch.setattr(outlook, "run", fail_run)
    result = refresh.ensure_fresh(state_file=str(state), now_ts=1000, exchange=object())
    assert result["ok"] is True
    assert result["refreshed"] is False
    assert result["reason"] == "FRESH"


def test_stale_snapshot_refreshes_without_telegram(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    _write(state, 100)
    calls = {}

    def fake_run(exchange, **kwargs):
        calls.update(kwargs)
        _write(state, kwargs["current_ts"])
        return {"snapshot": {"ts": kwargs["current_ts"]}}

    monkeypatch.setattr(outlook, "run", fake_run)
    result = refresh.ensure_fresh(
        state_file=str(state),
        now_ts=2000,
        max_age_seconds=1200,
        exchange=object(),
    )
    assert result["ok"] is True
    assert result["refreshed"] is True
    assert result["reason"] == "REFRESHED"
    assert calls["allow_telegram"] is False
    assert calls["token"] is None
    assert calls["chat_id"] is None
