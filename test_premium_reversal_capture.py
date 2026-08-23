import json
from pathlib import Path

import pandas as pd

import premium_reversal_capture as reversal


NOW = 1_787_480_000
SYMBOL = "FARTCOINUSDT"


class FakeBot:
    def __init__(self, performance):
        self._performance = performance

    def load_performance(self):
        return self._performance


def performance_with_tp3(direction="SHORT", closed_at=NOW - 3600):
    return {
        "days": {
            "2026-08-23": {
                "closed_times": {SYMBOL: closed_at},
                "closed_results": {SYMBOL: "TP3"},
                "closed_history": [
                    {
                        "time": "10:46:04",
                        "symbol": SYMBOL,
                        "direction": direction,
                        "result": "TP3",
                        "entry": 0.1724,
                        "exit": 0.16949,
                        "source": "TREND_CONTINUATION",
                        "score": 96,
                    }
                ],
            }
        }
    }


def fart_like_event(recorded_at=NOW - 60):
    return {
        "recorded_at": recorded_at,
        "symbol": SYMBOL,
        "direction": "LONG",
        "source": "SHADOW_TREND_CONTINUATION",
        "shadow_ready": False,
        "move15_percent": 0.6392,
        "move30_percent": 0.6392,
        "price": 0.1731,
        "ema20": 0.17176263342113923,
        "ema50": 0.172387059508775,
        "ema20_slope_percent": 0.221,
        "ema20_distance_percent": 0.7786,
        "green_5m_count": 2,
        "red_5m_count": 2,
        "resume_confirmed": True,
        "rsi5": 58.2044,
        "vol1": 3.793,
        "vol5": 0.4421,
        "existing_filter_missing": [
            "PUMP: 1M yeşil atak yetersiz",
            "PUMP: 5M hacim düşük",
        ],
        "trend_missing": [],
    }


def write_shadow(tmp_path: Path, event):
    path = tmp_path / "pump_state.json"
    path.write_text(json.dumps({"shadow_moves": [event]}), encoding="utf-8")
    return str(path)


def rising_frame(start, end, rows=100):
    closes = [start + (end - start) * i / (rows - 1) for i in range(rows)]
    data = []
    for i, close in enumerate(closes):
        spread = max(close * 0.0018, 0.00002)
        open_price = close - spread * 0.18
        data.append(
            {
                "open": open_price,
                "high": close + spread,
                "low": open_price - spread,
                "close": close,
                "volume": 1000.0 + i * 4.0,
            }
        )
    return pd.DataFrame(data)


def test_recent_tp3_context_finds_direction_and_opposite():
    reversal.reset_caches()
    bot = FakeBot(performance_with_tp3())
    context = reversal.recent_tp3_context(bot, SYMBOL, now_ts=NOW)
    assert context is not None
    assert context["direction"] == "SHORT"
    assert context["opposite_direction"] == "LONG"
    assert context["result"] == "TP3"


def test_reversal_probe_requires_fresh_opposite_shadow(tmp_path):
    reversal.reset_caches()
    bot = FakeBot(performance_with_tp3())
    state_file = write_shadow(tmp_path, fart_like_event())
    assert reversal.should_probe_reversal(
        bot,
        SYMBOL,
        state_file=state_file,
        now_ts=NOW,
    )


def test_same_direction_signal_is_not_promoted():
    context = {
        "direction": "SHORT",
        "opposite_direction": "LONG",
        "closed_at": NOW - 3600,
    }
    signal = {
        "symbol": SYMBOL,
        "direction": "SHORT",
        "source": "TREND_CONTINUATION",
        "score": 99,
        "risk_percent": 0.9,
    }
    assert reversal._promote_existing_reversal(signal, context) is None


def test_opposite_strong_signal_becomes_reversal():
    context = {
        "direction": "SHORT",
        "opposite_direction": "LONG",
        "closed_at": NOW - 3600,
    }
    signal = {
        "symbol": SYMBOL,
        "direction": "LONG",
        "source": "15M_ENTRY",
        "score": 98,
        "risk_percent": 0.9,
        "quality": "A+ ANA",
    }
    promoted = reversal._promote_existing_reversal(signal, context)
    assert promoted is not None
    assert promoted["direction"] == "LONG"
    assert promoted["source"] == reversal.SOURCE
    assert promoted["reversal_previous_direction"] == "SHORT"


def test_fart_like_tp3_reversal_produces_live_candidate(tmp_path):
    reversal.reset_caches()
    bot = FakeBot(performance_with_tp3())
    state_file = write_shadow(tmp_path, fart_like_event())

    # 15M is already turning up; 1H/4H are deliberately only mildly positive.
    # The route should not require a fully mature new 4H trend because that would
    # recreate the blind spot it is designed to solve.
    df15 = rising_frame(0.1696, 0.1730)
    df1h = rising_frame(0.1688, 0.1727)
    df4h = rising_frame(0.1660, 0.1720)

    signal = reversal.analyze_reversal(
        bot,
        SYMBOL,
        df15,
        df1h,
        df4h,
        0.1731,
        state_file=state_file,
        now_ts=NOW,
    )

    assert signal is not None
    assert signal["direction"] == "LONG"
    assert signal["source"] == reversal.SOURCE
    assert signal["score"] >= reversal.MIN_SCORE
    assert signal["sl"] < signal["entry"] < signal["tp1"] < signal["tp2"] < signal["tp3"]
    assert signal["reversal_previous_direction"] == "SHORT"
