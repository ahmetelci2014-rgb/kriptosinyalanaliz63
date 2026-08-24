import json

import crypto_universe_guard as guard
import untradable_futures_cleanup as clean


def dump(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def wire_files(tmp_path, monkeypatch):
    open_file = tmp_path / "open.json"
    ledger_file = tmp_path / "ledger.json"
    pending_file = tmp_path / "pending.json"
    audit_file = tmp_path / "audit.json"
    monkeypatch.setattr(clean, "OPEN_FILE", str(open_file))
    monkeypatch.setattr(clean, "LEDGER_FILE", str(ledger_file))
    monkeypatch.setattr(clean, "PENDING_FILE", str(pending_file))
    monkeypatch.setattr(clean, "AUDIT_FILE", str(audit_file))
    return open_file, ledger_file, pending_file, audit_file


def test_cleanup_removes_ethw_and_invalidates_ledger(tmp_path, monkeypatch):
    guard.clear_account_tradable_futures()
    monkeypatch.setattr(
        guard,
        "refresh_account_tradable_futures_from_env",
        lambda force=False: False,
    )
    open_file, ledger_file, pending_file, audit_file = wire_files(tmp_path, monkeypatch)

    dump(open_file, {
        "ETHW_KEY": {
            "trade_id": "ETHW_TRADE",
            "symbol": "ETHWUSDT",
            "direction": "LONG",
            "source": "EARLY_BREAKOUT_ENTRY",
        },
        "BTC_KEY": {
            "trade_id": "BTC_TRADE",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "source": "15M_ENTRY",
        },
    })
    dump(ledger_file, {
        "trades": {
            "ETHW_TRADE": {
                "trade_id": "ETHW_TRADE",
                "symbol": "ETHWUSDT",
                "status": "OPEN",
                "events": [],
            },
            "BTC_TRADE": {
                "trade_id": "BTC_TRADE",
                "symbol": "BTCUSDT",
                "status": "OPEN",
                "events": [],
            },
        }
    })
    dump(pending_file, {
        "pending": {
            "ETHW|LONG": {"signal": {"symbol": "ETHWUSDT"}},
            "BTC|LONG": {"signal": {"symbol": "BTCUSDT"}},
        }
    })

    result = clean.cleanup(now_ts=1_787_570_000)

    assert result["removed_open"] == ["ETHW_KEY"]
    assert result["removed_pending"] == ["ETHW|LONG"]
    assert result["invalidated_ledger"] == ["ETHW_TRADE"]

    open_state = load(open_file)
    assert "ETHW_KEY" not in open_state
    assert "BTC_KEY" in open_state

    pending_state = load(pending_file)
    assert "ETHW|LONG" not in pending_state["pending"]
    assert "BTC|LONG" in pending_state["pending"]

    ledger = load(ledger_file)
    ethw = ledger["trades"]["ETHW_TRADE"]
    assert ethw["status"] == "INVALID"
    assert ethw["final_result"] == "INVALID_MARKET_UNTRADABLE"
    assert ethw["r_result"] is None
    assert ethw["invalid_market"]["performance_eligible"] is False
    assert ledger["trades"]["BTC_TRADE"]["status"] == "OPEN"

    audit = load(audit_file)
    assert audit["records"][0]["trade_id"] == "ETHW_TRADE"
    assert audit["records"][0]["performance_eligible"] is False
    guard.clear_account_tradable_futures()


def test_account_allowlist_removes_every_unavailable_symbol(tmp_path, monkeypatch):
    open_file, ledger_file, pending_file, audit_file = wire_files(tmp_path, monkeypatch)

    def fake_refresh(force=False):
        guard.set_account_tradable_futures({"BTCUSDT", "DOGEUSDT"})
        return True

    monkeypatch.setattr(
        guard,
        "refresh_account_tradable_futures_from_env",
        fake_refresh,
    )

    dump(open_file, {
        "BTC": {"trade_id": "BTC_T", "symbol": "BTCUSDT", "direction": "LONG"},
        "ALT": {"trade_id": "ALT_T", "symbol": "ALTUSDT", "direction": "LONG"},
    })
    dump(ledger_file, {
        "trades": {
            "BTC_T": {"trade_id": "BTC_T", "symbol": "BTCUSDT", "status": "OPEN", "events": []},
            "ALT_T": {"trade_id": "ALT_T", "symbol": "ALTUSDT", "status": "OPEN", "events": []},
        }
    })
    dump(pending_file, {
        "pending": {
            "DOGE|LONG": {"signal": {"symbol": "DOGEUSDT"}},
            "ALT|LONG": {"signal": {"symbol": "ALTUSDT"}},
        }
    })

    result = clean.cleanup(now_ts=1_787_570_000)
    assert result["removed_open"] == ["ALT"]
    assert result["removed_pending"] == ["ALT|LONG"]
    assert result["invalidated_ledger"] == ["ALT_T"]

    ledger = load(ledger_file)
    assert ledger["trades"]["ALT_T"]["invalid_market"]["reason"] == "ACCOUNT_FUTURES_NOT_AVAILABLE"
    assert ledger["trades"]["BTC_T"]["status"] == "OPEN"

    audit = load(audit_file)
    assert audit["account_allowlist_active"] is True
    assert audit["account_tradable_futures_count"] == 2
    guard.clear_account_tradable_futures()
