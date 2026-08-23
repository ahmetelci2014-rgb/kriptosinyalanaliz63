from market_outlook_report_v2 import build_message, scenario_weights


def _ref(price, scores, rsi=60.0, atr=2.0):
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
            "macro_support": 65000,
            "macro_resistance": 83000,
        },
    }


def sample_snapshot():
    return {
        "references": {
            "BTCUSDT": _ref(77200, {"15m": 30, "1h": 70, "4h": 80, "1d": 55}, rsi=71.7, atr=2.4),
            "ETHUSDT": _ref(2427, {"15m": 10, "1h": 50, "4h": 60, "1d": 35}),
            "SOLUSDT": _ref(94.56, {"15m": 20, "1h": 45, "4h": 65, "1d": 30}),
        },
        "breadth": {
            "eligible": 274,
            "up_pct": 34.3,
            "down_pct": 59.6,
            "flat_pct": 6.1,
            "median_change": -1.01,
            "volume_weighted_change": 0.44,
            "top": [
                {"symbol": "AAAUSDT", "change": 8.2},
                {"symbol": "BBBUSDT", "change": 6.1},
                {"symbol": "CCCUSDT", "change": 5.2},
            ],
            "bottom": [
                {"symbol": "XXXUSDT", "change": -7.4},
                {"symbol": "YYYUSDT", "change": -5.8},
                {"symbol": "ZZZUSDT", "change": -4.3},
            ],
        },
        "derivatives": {
            "funding": {"BTCUSDT": 0.0001, "ETHUSDT": 0.00008, "SOLUSDT": 0.00012},
            "funding_average": 0.0001,
            "oi_change_since_last_run_percent": {"BTCUSDT": 1.2, "ETHUSDT": 0.4, "SOLUSDT": -0.3},
        },
        "outlook": {
            "score_6h": 66,
            "score_24h": 71,
            "bias_6h": "GÜÇLÜ YUKARI",
            "bias_24h": "GÜÇLÜ YUKARI",
            "direction_6h": "UP",
            "direction_24h": "UP",
            "confidence_6h": 77,
            "confidence_24h": 79,
            "long_suitability": 8,
            "short_suitability": 2,
            "risk_flags": ["Majör yükselişi altcoin geneline tam yayılmıyor"],
        },
    }


def test_scenario_weights_are_bounded_and_sum_to_100():
    up, flat, down = scenario_weights(sample_snapshot()["outlook"])
    assert up + flat + down == 100
    assert up > down
    assert min(up, flat, down) >= 5


def test_detailed_report_has_decision_sections():
    state = {
        "accuracy": {
            "6h": {"sample": 8, "accuracy_percent": 75.0},
            "24h": {"sample": 7, "accuracy_percent": 71.4},
        }
    }
    message = build_message(sample_snapshot(), state)
    for text in (
        "GENEL PİYASA DEĞERLENDİRMESİ — V2",
        "Piyasa rejimi",
        "24S model senaryo ağırlığı",
        "ÇOKLU ZAMAN DİLİMİ",
        "BTC SENARYO HARİTASI",
        "ALTCOIN BREADTH",
        "TÜREV PİYASA",
        "BUGÜN İÇİN YAKLAŞIM",
        "MODEL TAKİBİ",
        "Görüş bozulması",
    ):
        assert text in message
    assert "SEÇİCİ MAJÖR RALLİSİ" in message
    assert "AAA +8.20%" in message
    assert "XXX -7.40%" in message
    assert len(message) < 4096


def test_scenario_weights_are_labeled_as_not_calibrated_probability():
    message = build_message(sample_snapshot(), {"accuracy": {}})
    assert "kalibre edilmiş olasılık değildir" in message
