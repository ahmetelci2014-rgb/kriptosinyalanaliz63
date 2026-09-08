from pathlib import Path

import pandas as pd

import market_first_swing_2h as swing
import market_first_swing_2h_tracking_fix as fix


def _plan(**overrides):
    plan = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "current_price": 105.0,
        "score": 82,
        "zone_low": 100.0,
        "zone_high": 101.0,
        "ideal_entry": 100.5,
        "sl": 99.0,
        "tp1": 101.5,
        "tp2": 102.0,
        "tp3": 103.0,
        "structure_2h": "LONG",
        "structure_1h": "LONG",
        "structure_15m": "SHORT",
        "structure_5m": "SHORT",
    }
    plan.update(overrides)
    return plan


def _bar(high, low):
    return pd.DataFrame([{"high": high, "low": low}])


def test_new_swing_does_not_count_target_before_entry_zone_touch():
    ledger = {"episodes": {}}
    episode = fix.register_plan_v2(ledger, _plan(), now=1000, alerted=False)

    assert episode["entry_tracking_version"] == fix.TRACKING_VERSION
    assert episode["entry_activated"] is False
    assert episode["tracking_entry_reference"] == 100.5

    # Price is already above TP1, but the planned pullback entry zone was never hit.
    fix.update_symbol_market_v2(
        ledger,
        "TESTUSDT",
        current_price=105.2,
        df5m=_bar(106.0, 104.0),
        now=1100,
    )
    assert episode["entry_activated"] is False
    assert episode["tp1_at"] == 0
    assert episode["first_decisive_event"] is None


def test_entry_touch_activates_then_next_bar_can_count_tp1():
    ledger = {"episodes": {}}
    episode = fix.register_plan_v2(ledger, _plan(), now=1000, alerted=False)

    # Entry-touch candle is deliberately not used for target sequencing.
    fix.update_symbol_market_v2(
        ledger,
        "TESTUSDT",
        current_price=101.2,
        df5m=_bar(102.0, 100.8),
        now=1100,
    )
    assert episode["entry_activated"] is True
    assert episode["entry_at"] == 1100
    assert episode["tp1_at"] == 0

    fix.update_symbol_market_v2(
        ledger,
        "TESTUSDT",
        current_price=101.4,
        df5m=_bar(101.7, 100.9),
        now=1400,
    )
    assert episode["tp1_at"] == 1400
    assert episode["first_decisive_event"] == "TP1_FIRST"


def test_no_entry_resolves_as_no_entry_timeout_not_fake_loss_or_win():
    ledger = {"episodes": {}}
    episode = fix.register_plan_v2(ledger, _plan(), now=1000, alerted=False)
    resolved = fix.finalize_expired_v2(
        ledger,
        now=1000 + swing.ACTIVE_TRACK_SECONDS,
    )
    assert resolved == 1
    assert episode["resolved"] is True
    assert episode["outcome"] == "NO_ENTRY_TIMEOUT"
    assert episode["tp1_at"] == 0
    assert episode["sl_at"] == 0


def test_post_entry_tracking_window_starts_from_entry_touch():
    ledger = {"episodes": {}}
    episode = fix.register_plan_v2(ledger, _plan(), now=1000, alerted=False)
    episode["entry_activated"] = True
    episode["entry_at"] = 1000 + swing.ACTIVE_TRACK_SECONDS - 60

    # Preparation is older than 36h, but the entry happened only one minute ago.
    resolved = fix.finalize_expired_v2(
        ledger,
        now=1000 + swing.ACTIVE_TRACK_SECONDS,
    )
    assert resolved == 0
    assert episode["resolved"] is False


def test_active_symbol_priority_rotates_by_least_recent_update():
    episodes = {}
    for index in range(20):
        symbol = f"C{index:02d}USDT"
        episodes[symbol] = {
            "symbol": symbol,
            "direction": "LONG",
            "first_at": 1000,
            "updated_at": 5000 - index * 10,
            "resolved": False,
            "entry_tracking_version": fix.TRACKING_VERSION,
            "entry_activated": True,
            "entry_at": 1000,
        }
    result = fix.active_symbols_v2({"episodes": episodes}, now=2000)

    assert len(result) == swing.MAX_ACTIVE_PRIORITY
    # Most stale (smallest updated_at) symbols are selected first, not insertion order.
    assert result[0] == "C19USDT"
    assert "C00USDT" not in result


def test_summary_separates_legacy_from_corrected_v2_metrics():
    ledger = {
        "episodes": {
            "legacy": {
                "symbol": "OLDUSDT",
                "direction": "LONG",
                "first_at": 1,
                "resolved": True,
                "first_decisive_event": "TP1_FIRST",
                "tp3_at": 10,
                "best_favorable_percent": 5.0,
            },
            "v2win": {
                "symbol": "NEW1USDT",
                "direction": "LONG",
                "first_at": 2,
                "resolved": False,
                "entry_tracking_version": fix.TRACKING_VERSION,
                "entry_activated": True,
                "entry_at": 3,
                "first_decisive_event": "TP1_FIRST",
            },
            "v2wait": {
                "symbol": "NEW2USDT",
                "direction": "SHORT",
                "first_at": 4,
                "resolved": False,
                "entry_tracking_version": fix.TRACKING_VERSION,
                "entry_activated": False,
                "first_decisive_event": None,
            },
        }
    }
    summary = fix.summary_v2(ledger, now=100)

    assert summary["legacy_episode_count"] == 1
    assert summary["v2_episode_count"] == 2
    assert summary["v2_entry_activated"] == 1
    assert summary["v2_waiting_entry"] == 1
    assert summary["tracking_open"] == 2
    assert summary["tracking_decided"] == 1
    assert summary["tracking_undecided"] == 1
    assert summary["v2_tp1_first_rate_decided_ex_ambiguous"] == 1.0


def test_live_entry_point_installs_swing_fix_before_complete_tracking():
    text = Path("market_first_live_simple.py").read_text(encoding="utf-8")
    assert "import market_first_swing_2h_tracking_fix as swing_tracking_fix" in text
    assert text.index("swing_tracking_fix.install()") < text.index(
        "complete_tracking.install_complete_tracking()"
    )
