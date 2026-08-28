import pandas as pd

import simple_core_paper_alerts as paper


def _frame15():
    rows = []
    for i in range(45):
        rows.append({
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "atr": 1.0,
        })
    return pd.DataFrame(rows)


def _install_common(monkeypatch, *, rejection_ok, trigger_ok):
    monkeypatch.setattr(
        paper.core,
        "_one_hour_direction",
        lambda df: (
            "LONG",
            "1H yükseliş trendi",
            {"adx_1h": 28.0, "rsi_1h": 56.0},
        ),
    )
    monkeypatch.setattr(
        paper.core.indicators,
        "add_indicators",
        lambda df: df,
    )
    monkeypatch.setattr(
        paper.core,
        "_find_zone",
        lambda direction, frame15, entry, atr_percent: (
            99.8,
            0.2,
            "15M swing destek",
        ),
    )
    monkeypatch.setattr(
        paper.core,
        "_fifteen_minute_rejection",
        lambda direction, frame15, zone, atr_percent: (
            rejection_ok,
            "15M destekten bullish reddedilme",
        ),
    )
    monkeypatch.setattr(
        paper.core,
        "_five_minute_trigger",
        lambda direction, df5m: (
            trigger_ok,
            "5M önceki 2 mum tepesini bullish kırdı",
            {
                "volume_5m": 1.4,
                "rsi_5m": 58.0,
                "close_power_5m": 70.0,
            },
        ),
    )
    monkeypatch.setattr(
        paper.core,
        "_targets_and_room",
        lambda direction, frame15, entry, zone, atr: (
            {
                "sl": 99.0,
                "tp1": 100.75,
                "tp2": 101.25,
                "tp3": 102.0,
                "risk_percent": 1.0,
                "rr_tp1": 0.75,
                "rr_tp2": 1.25,
                "rr_tp3": 2.0,
                "room_r": 3.2,
                "opposing_level": 103.2,
            },
            "OK",
        ),
    )


def test_paper_candidate_allows_one_missing_5m_gate(monkeypatch):
    _install_common(monkeypatch, rejection_ok=True, trigger_ok=False)
    candidate, reason = paper.build_paper_candidate(
        "TESTUSDT",
        object(),
        _frame15(),
        object(),
        100.0,
    )

    assert reason == "PAPER_READY"
    assert candidate is not None
    assert candidate["paper_missing_gate"] == "5M_NO_CONFIRM"
    assert candidate["missing_gate_count"] == 1
    assert candidate["signal_class"] == "PAPER"
    assert candidate["room_r"] == 3.2


def test_paper_candidate_allows_one_missing_15m_gate(monkeypatch):
    _install_common(monkeypatch, rejection_ok=False, trigger_ok=True)
    candidate, reason = paper.build_paper_candidate(
        "TESTUSDT",
        object(),
        _frame15(),
        object(),
        100.0,
    )

    assert reason == "PAPER_READY"
    assert candidate is not None
    assert candidate["paper_missing_gate"] == "15M_NO_REJECTION"
    assert candidate["missing_gate_count"] == 1


def test_paper_candidate_allows_two_missing_late_gates_for_observation(monkeypatch):
    _install_common(monkeypatch, rejection_ok=False, trigger_ok=False)
    candidate, reason = paper.build_paper_candidate(
        "TESTUSDT",
        object(),
        _frame15(),
        object(),
        100.0,
    )

    assert reason == "PAPER_READY"
    assert candidate is not None
    assert candidate["paper_missing_gate"] == "15M_NO_REJECTION+5M_NO_CONFIRM"
    assert candidate["missing_gate_count"] == 2
    assert candidate["score"] < 85


def test_paper_candidate_does_not_duplicate_live_ready_setup(monkeypatch):
    _install_common(monkeypatch, rejection_ok=True, trigger_ok=True)
    candidate, reason = paper.build_paper_candidate(
        "TESTUSDT",
        object(),
        _frame15(),
        object(),
        100.0,
    )

    assert candidate is None
    assert reason == "LIVE_READY"


def test_paper_message_is_unambiguously_test_only():
    class Bot:
        @staticmethod
        def format_price(value):
            return str(value)

    message = paper.format_paper_message(
        Bot(),
        {
            "symbol": "TESTUSDT",
            "direction": "LONG",
            "entry": 100.0,
            "ideal_entry": 99.8,
            "zone_distance_percent": 0.2,
            "sl": 99.0,
            "risk_percent": 1.0,
            "tp1": 100.75,
            "tp2": 101.25,
            "tp3": 102.0,
            "room_r": 3.2,
            "trend_reason": "1H yükseliş trendi",
            "paper_missing_text": "15M bölge dönüşü ve 5M giriş kırılım teyidi henüz eksik",
            "score": 70,
        },
    )

    assert "TEST / PAPER" in message
    assert "GERÇEK İŞLEM DEĞİL" in message
    assert "canlı işlem sinyali" in message
