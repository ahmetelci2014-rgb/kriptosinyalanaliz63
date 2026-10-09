"""Regression tests: no candle that opened before a candidate alert may be used."""
from __future__ import annotations

import pandas as pd
import market_first_candidate_shadow as shadow


START = 1_800_000_000  # divisible by 60


def _trade():
    # Alert halfway through the 01:00 candle; first eligible candle is 02:00.
    return {"opened_at": START + 90, "last_checked_at": START + 90,
            "last_bar_ts": START + 60}


def _frame(*, timed: bool):
    values = [
        {"high": 108.0, "low": 90.0, "close": 100.0},  # pre-alert
        {"high": 108.0, "low": 90.0, "close": 100.0},  # partial alert minute
        {"high": 101.0, "low": 99.0, "close": 100.0},  # first safe candle
        {"high": 101.0, "low": 99.0, "close": 100.0},  # still open
    ]
    if timed:
        for i, item in enumerate(values):
            item["time"] = (START + 60 * i) * 1000
    return pd.DataFrame(values)


def test_timestamped_candles_exclude_before_alert_and_partial_alert_minute():
    bars = shadow._recent_closed_bars(_frame(timed=True), _trade(), START + 195)
    assert len(bars) == 1
    assert bars[0]["ts"] == START + 120
    assert bars[0]["high"] == 101.0


def test_timestamp_free_fallback_does_not_relabel_old_bars_as_new():
    assert shadow._recent_closed_bars(_frame(timed=False), _trade(), START + 170) == []
    bars = shadow._recent_closed_bars(_frame(timed=False), _trade(), START + 195)
    assert len(bars) == 1
    assert bars[0]["ts"] == START + 120
    assert bars[0]["high"] == 101.0


def test_only_new_policy_counts_as_validated_cohort():
    ledger = {"trades": {
        "legacy": {"status": "CLOSED", "first_result": "TP1_FIRST",
                   "final_result": "TP3", "max_target_hit": 3},
        "new_win": {"status": "CLOSED", "first_result": "TP1_FIRST",
                    "final_result": "TP3", "max_target_hit": 3,
                    "tracking_policy": shadow.TRACKING_POLICY},
        "new_loss": {"status": "CLOSED", "first_result": "SL_FIRST",
                     "final_result": "SL_FIRST", "max_target_hit": 0,
                     "tracking_policy": shadow.TRACKING_POLICY},
    }}
    report = shadow.build_summary(ledger)
    assert report["total"] == 3
    assert report["legacy_unverified_count"] == 1
    assert report["post_fix_cohort"] == {
        "tracking_policy": shadow.TRACKING_POLICY,
        "total": 2, "decided": 2, "tp1_first": 1, "sl_first": 1,
        "tp1_first_rate": 0.5, "tp3_reached": 1,
    }
