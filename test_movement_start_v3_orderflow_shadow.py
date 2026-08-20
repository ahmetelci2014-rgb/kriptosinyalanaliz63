import json

import pandas as pd

import movement_start_v3_orderflow_shadow as v3


def _flow(long=True):
    sign = 1 if long else -1
    return {
        "captured_at": 1_000_000,
        "inst_id": "TEST-USDT-SWAP",
        "book_imbalance": 0.28 * sign,
        "top_imbalance": 0.20 * sign,
        "spread_bps": 4.0,
        "trade_imbalance": 0.31 * sign,
        "recent_trade_imbalance": 0.36 * sign,
        "buy_ratio": 0.68 if long else 0.32,
        "buy_count_ratio": 0.64 if long else 0.36,
        "trades_count": 100,
        "trades_per_second": 3.2,
    }


def _base(direction="LONG", stage="ARMED", score=82):
    entry = 100.0
    stop = 99.0 if direction == "LONG" else 101.0
    risk = 1.0
    return {
        "symbol": "TESTUSDT",
        "direction": direction,
        "stage": stage,
        "score": score,
        "entry": entry,
        "stop": stop,
        "risk_abs": risk,
        "risk_percent": 1.0,
        "target_2r": 102.0 if direction == "LONG" else 98.0,
        "target_3r": 103.0 if direction == "LONG" else 97.0,
        "target_5r": 105.0 if direction == "LONG" else 95.0,
    }


def _bars(high=100.2, low=99.8):
    return pd.DataFrame(
        [
            {"open": 100.0, "high": high, "low": low, "close": 100.0, "volume": 1.0},
            {"open": 100.0, "high": high, "low": low, "close": 100.0, "volume": 1.0},
            {"open": 100.0, "high": high, "low": low, "close": 100.0, "volume": 1.0},
        ]
    )


def test_okx_swap_symbol_normalization():
    assert v3.okx_inst_id("RAYUSDT") == "RAY-USDT-SWAP"
    assert v3.okx_inst_id("BTC/USDT:USDT") == "BTC-USDT-SWAP"
    assert v3.okx_inst_id("ETH-USDT-SWAP") == "ETH-USDT-SWAP"


def test_query_gate_starts_near_armed_not_every_prep():
    assert not v3.should_query(_base(stage="PREP", score=68))
    assert v3.should_query(_base(stage="PREP", score=72))
    assert v3.should_query(_base(stage="ARMED", score=76))
    assert v3.should_query(_base(stage="TRIGGER", score=88))


def test_directional_orderflow_scoring():
    long_score, long_conditions, _ = v3.score_order_flow(_flow(True), "LONG")
    wrong_score, _, _ = v3.score_order_flow(_flow(True), "SHORT")
    short_score, short_conditions, _ = v3.score_order_flow(_flow(False), "SHORT")
    assert long_score >= v3.CONFIRM_SCORE
    assert short_score >= v3.CONFIRM_SCORE
    assert wrong_score < long_score
    assert long_conditions["book_support"] and long_conditions["recent_trade_support"]
    assert short_conditions["book_support"] and short_conditions["recent_trade_support"]


def test_pressure_delta_rewards_improving_book():
    previous = _flow(True)
    previous["book_imbalance"] = 0.10
    current = _flow(True)
    score, conditions, delta = v3.score_order_flow(current, "LONG", previous)
    assert delta >= 0.04
    assert conditions["pressure_improving"]
    assert score >= v3.CONFIRM_SCORE


def test_shadow_lifecycle_tracks_2r_without_live_execution(tmp_path):
    path = tmp_path / "v3.json"
    v3.begin(str(path))
    start = v3.observe(
        "TESTUSDT",
        _base(),
        _bars(),
        100.0,
        now_ts=1_000_000,
        fetcher=lambda symbol, now: _flow(True),
    )
    assert start and start["event"] == "START"
    assert start["snapshot"]["orderflow_confirmed"] is True

    # Son kapanmış 5M mum 2R üstünü görsün; yeni order-flow sorgusuna gerek yok.
    v3.observe(
        "TESTUSDT",
        None,
        _bars(high=102.2, low=99.8),
        102.1,
        now_ts=1_000_301,
        fetcher=lambda symbol, now: None,
    )
    summary = v3.finish(now_ts=1_011_000)
    assert summary["r2_first"] == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["records"][0]["status"] == "R2_FIRST"


def test_module_declares_shadow_only_and_no_execution_api():
    assert "NO_TELEGRAM_NO_ORDERS" in v3.MODE
    assert not hasattr(v3, "send_telegram")
    assert not hasattr(v3, "place_order")
