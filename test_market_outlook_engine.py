from datetime import datetime

import market_outlook_engine as m


def _refs(score_15=70, score_1h=70, score_4h=70, score_1d=60):
    result = {}
    for symbol in m.REFERENCE_SYMBOLS:
        result[symbol] = {
            "scores": {"15m": score_15, "1h": score_1h, "4h": score_4h, "1d": score_1d},
            "atr_4h_percent": 1.2,
        }
    return result


def test_bullish_outlook_scores_long_above_short():
    breadth = {"up_pct": 68, "down_pct": 25, "median_change": 1.2, "volume_weighted_change": 1.0}
    derivatives = {"funding_average": 0.0001}
    out = m.compute_outlook(_refs(), breadth, derivatives)
    assert out["direction_6h"] == "UP"
    assert out["direction_24h"] == "UP"
    assert out["long_suitability"] > out["short_suitability"]


def test_bearish_outlook_scores_short_above_long():
    refs = _refs(-70, -70, -75, -60)
    breadth = {"up_pct": 20, "down_pct": 72, "median_change": -1.4, "volume_weighted_change": -1.2}
    out = m.compute_outlook(refs, breadth, {"funding_average": -0.0001})
    assert out["direction_6h"] == "DOWN"
    assert out["direction_24h"] == "DOWN"
    assert out["short_suitability"] > out["long_suitability"]


def test_daily_report_is_once_per_turkey_date():
    ts = int(datetime(2026, 8, 23, 6, 5, tzinfo=m.timezone.utc).timestamp())  # 09:05 TR
    state = m.empty_state()
    assert m.should_send_daily(state, ts) is True
    state["last_report_date"] = "2026-08-23"
    assert m.should_send_daily(state, ts) is False


def test_forecast_outcome_is_evaluated_after_due_time():
    state = m.empty_state()
    created = 1_000_000
    state["forecasts"] = [{
        "id": "X", "created_at": created, "btc_entry": 100.0,
        "direction_6h": "UP", "direction_24h": "UP",
    }]
    m.evaluate_forecasts(state, 101.0, created + 6 * 3600)
    assert state["forecasts"][0]["outcome_6h"]["actual"] == "UP"
    assert state["forecasts"][0]["outcome_6h"]["correct"] is True
    assert "outcome_24h" not in state["forecasts"][0]


def test_funding_crowding_reduces_confidence():
    normal = m.confidence(60, 0.0001, 1.0)
    crowded = m.confidence(60, 0.0010, 1.0)
    assert crowded < normal
