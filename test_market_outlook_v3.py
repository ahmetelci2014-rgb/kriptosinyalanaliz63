from market_outlook_research_v3 import derive_research
from market_outlook_report_v3 import build_message


def ref(price, scores, rsi=60.0, atr=2.0):
    return {
        "price": price,
        "scores": scores,
        "rsi_4h": rsi,
        "atr_4h_percent": atr,
        "levels": {
            "support1": 75500,
            "support2": 69150,
            "resistance1": 77585,
            "resistance2": 79600,
            "macro_support": 62000,
            "macro_resistance": 83000,
        },
    }


def snapshot(ts=100_000, btc=77536, up=62.0, score6=84, score24=86, top=None):
    return {
        "ts": ts,
        "references": {
            "BTCUSDT": ref(btc, {"15m": 90, "1h": 95, "4h": 89, "1d": 100}, rsi=71.7, atr=1.63),
            "ETHUSDT": ref(2464, {"15m": 92, "1h": 96, "4h": 88, "1d": 100}),
            "SOLUSDT": ref(95.53, {"15m": 91, "1h": 97, "4h": 90, "1d": 100}),
        },
        "breadth": {
            "eligible": 265,
            "up_pct": up,
            "down_pct": 100 - up - 4,
            "flat_pct": 4,
            "median_change": 1.03,
            "volume_weighted_change": 3.64,
            "top": top or [
                {"symbol": "ZROUSDT", "change": 24.8},
                {"symbol": "TRUMPUSDT", "change": 20.3},
                {"symbol": "MORPHOUSDT", "change": 18.2},
                {"symbol": "PENDLEUSDT", "change": 16.0},
                {"symbol": "STXUSDT", "change": 15.0},
            ],
            "bottom": [
                {"symbol": "GRVTUSDT", "change": -12.8},
                {"symbol": "BEATUSDT", "change": -10.5},
            ],
        },
        "derivatives": {
            "funding_average": 0.0001,
            "funding": {"BTCUSDT": 0.0001, "ETHUSDT": 0.0001, "SOLUSDT": 0.0001},
            "oi_change_since_last_run_percent": {"BTCUSDT": -0.2, "ETHUSDT": -0.3, "SOLUSDT": -0.1},
        },
        "outlook": {
            "score_6h": score6,
            "score_24h": score24,
            "bias_6h": "GÜÇLÜ YUKARI",
            "bias_24h": "GÜÇLÜ YUKARI",
            "direction_6h": "UP",
            "direction_24h": "UP",
            "confidence_6h": 84,
            "confidence_24h": 85,
            "long_suitability": 9,
            "short_suitability": 1,
            "risk_flags": [],
        },
    }


def sample_state():
    old12 = snapshot(ts=100_000 - 12 * 3600 - 60, btc=74800, up=38.0, score6=58, score24=64)
    old6 = snapshot(ts=100_000 - 6 * 3600 - 60, btc=76000, up=42.0, score6=65, score24=70)
    old6["breadth"]["top"] = [
        {"symbol": "ZROUSDT", "change": 12},
        {"symbol": "TRUMPUSDT", "change": 10},
        {"symbol": "AAAUSDT", "change": 9},
        {"symbol": "BBBUSDT", "change": 8},
        {"symbol": "CCCUSDT", "change": 7},
    ]
    old2 = snapshot(ts=100_000 - 2 * 3600 - 60, btc=77000, up=55.0, score6=78, score24=80)
    current = snapshot()
    return {
        "snapshots": [old12, old6, old2, current],
        "accuracy": {
            "6h": {"sample": 8, "accuracy_percent": 75.0},
            "24h": {"sample": 7, "accuracy_percent": 71.4},
        },
    }, current


def test_research_uses_history_and_detects_expanding_risk_on():
    state, current = sample_state()
    research = derive_research(current, state)
    assert research["pulse"] == "YÜKSELİŞ GENİŞ TABANA YAYILIYOR"
    assert research["sampling_hours"] == 2
    assert research["changes"]["breadth_6h"] is not None
    assert research["changes"]["breadth_6h"] > 0
    assert research["changes"]["btc_6h"] > 0
    assert research["changes"]["btc_12h"] > 0
    assert research["alignment"]["up"] == 12
    assert research["heat"]["label"] == "ısınmış"
    assert len(research["evidence"]) >= 4


def test_v3_report_is_clear_detailed_and_under_telegram_limit():
    state, current = sample_state()
    message = build_message(current, state)
    for text in (
        "GENEL PİYASA DEĞERLENDİRMESİ",
        "GENEL GÖRÜNÜM",
        "Piyasa ne yapıyor?",
        "24S senaryo",
        "BTC YOL HARİTASI",
        "ALTCOINLER",
        "VADELİ PİYASA",
        "ANA RİSKLER",
        "BUGÜNÜN YAKLAŞIMI",
        "MODEL TAKİBİ",
        "Arka plan araştırması",
        "tarama 2 saatte bir",
    ):
        assert text in message
    assert "ZRO %+24.80" in message
    assert "LONG tarafı öncelikli" in message
    assert len(message) < 4096


def test_research_flags_narrow_breadth_when_majors_are_up():
    state, current = sample_state()
    current = snapshot(up=34.0)
    state["snapshots"][-1] = current
    research = derive_research(current, state)
    assert research["pulse"] == "MAJÖRLER GÜÇLÜ, ALTCOINLER GERİDE"
    assert any("altcoin geneline" in risk for risk in research["risks"])
