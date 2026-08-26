import pandas as pd

import market_structure_ai_shadow as ms


def _frame(closes, volumes=None, width=0.6):
    if volumes is None:
        volumes = [100.0] * len(closes)
    rows = []
    prev = float(closes[0])
    for close, volume in zip(closes, volumes):
        close = float(close)
        rows.append(
            {
                "open": prev,
                "high": max(prev, close) + width,
                "low": min(prev, close) - width,
                "close": close,
                "volume": float(volume),
            }
        )
        prev = close
    return pd.DataFrame(rows)


def test_confirmed_pivots_find_real_swings():
    frame = _frame([10, 9.7, 9.2, 9.8, 10.4, 10.0, 9.5, 10.1, 10.7, 10.2], width=0.1)
    lows, highs = ms._find_pivots(frame)
    assert lows
    assert highs
    assert min(p["price"] for p in lows) <= 9.1
    assert max(p["price"] for p in highs) >= 10.7


def test_aeon_style_structure_can_be_ready_near_origin_even_if_15m_opposes():
    features = {
        "prior_downtrend": True,
        "prior_uptrend": False,
        "higher_low": True,
        "lower_high": True,
        "support_touches": 2,
        "resistance_touches": 1,
        "sweep_long": True,
        "sweep_short": False,
        "double_bottom": True,
        "double_top": False,
        "trendline_break_long": True,
        "trendline_break_short": False,
        "choch_long": True,
        "choch_short": False,
        "bos_long": True,
        "bos_short": False,
        "volume_wake": True,
        "impulse_long": True,
        "impulse_short": False,
        "ema_turn_long": True,
        "ema_turn_short": False,
        # Eski büyük zaman yönü yerel dönüşü veto etmemeli.
        "fifteen_long_ok": False,
        "fifteen_short_ok": True,
        "origin_long_distance_atr": 1.35,
        "origin_short_distance_atr": 6.0,
    }
    long_score, long_conditions = ms.score_direction(features, "LONG")
    short_score, _ = ms.score_direction(features, "SHORT")
    assert long_score >= ms.READY_SCORE
    assert long_score - short_score >= ms.MIN_DIRECTION_GAP
    assert ms._stage(long_score, long_conditions, 1.35) == "READY"


def test_ready_is_blocked_after_move_is_already_far_from_origin():
    features = {
        "prior_downtrend": True,
        "prior_uptrend": False,
        "higher_low": True,
        "lower_high": True,
        "support_touches": 3,
        "resistance_touches": 1,
        "sweep_long": True,
        "sweep_short": False,
        "double_bottom": True,
        "double_top": False,
        "trendline_break_long": True,
        "trendline_break_short": False,
        "choch_long": True,
        "choch_short": False,
        "bos_long": True,
        "bos_short": False,
        "volume_wake": True,
        "impulse_long": True,
        "impulse_short": False,
        "ema_turn_long": True,
        "ema_turn_short": False,
        "fifteen_long_ok": True,
        "fifteen_short_ok": False,
        "origin_long_distance_atr": 4.2,
        "origin_short_distance_atr": 7.0,
    }
    score, conditions = ms.score_direction(features, "LONG")
    assert ms._stage(score, conditions, 4.2) != "READY"


def test_feature_extractor_tracks_recent_origin_distance():
    # Kontrollü düşüş içinde küçük salınımlar gerçek swing high/low üretir.
    wiggle = [0.40, 0.05, -0.35, 0.00]
    base = [110 - i * 0.14 + wiggle[i % 4] for i in range(50)]
    reversal = [
        103.0, 102.2, 102.8, 101.9, 102.5, 101.5, 102.1, 101.2, 101.9, 100.8,
        101.5, 100.5, 101.2, 100.3, 101.1, 102.0, 101.4, 102.5, 103.4, 104.0,
    ]
    closes = base + reversal
    volumes = [100.0] * len(closes)
    volumes[-3:] = [145.0, 190.0, 240.0]
    # Son satır açık mum varsayımı için placeholder ekle.
    df5 = _frame(closes + [closes[-1]], volumes + [120.0], width=0.55)
    df15 = _frame([105 + 0.02 * i for i in range(61)], width=0.5)
    features = ms.extract_features(df5, df15, closes[-1])
    assert features is not None
    assert features["origin_long"] > 0
    assert features["origin_long_bars_ago"] <= 14
    assert features["origin_long_distance_atr"] >= 0
