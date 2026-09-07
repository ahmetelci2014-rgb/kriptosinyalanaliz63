import market_first_simple_mode as simple


def _strong_plan(**overrides):
    plan = {
        "symbol": "AAVEUSDT",
        "direction": "LONG",
        "current_price": 130.0,
        "zone_low": 129.8,
        "zone_high": 130.2,
        "zone_distance_percent": 0.0,
        "score": 86,
        "structure_5m": "NEUTRAL",
        "structure_15m": "LONG",
        "structure_1h": "LONG",
        "volume_ratio_5m": 0.72,
        "volume_ratio_15m": 0.91,
        "risk_percent": 0.88,
        "room_r": 2.1,
        "extension_atr_5m": 0.42,
        "market_preferred_direction": None,
        "sl": 128.85,
        "tp1": 130.86,
    }
    plan.update(overrides)
    return plan


def _strong_early(**overrides):
    decision = {
        "symbol": "LINKUSDT",
        "direction": "LONG",
        "stage": "EARLY",
        "score": 70,
        "current_price": 13.288,
        "move_1m_percent": 0.10,
        "move_3m_percent": 0.28,
        "move_5m_percent": 0.43,
        "volume_ratio_1m": 1.25,
        "relative_strength_5m": 0.35,
        "extension_atr_5m": 0.72,
        "structure_5m": "LONG",
        "structure_15m": "LONG",
        "structure_1h": "LONG",
        "market_preferred_direction": None,
    }
    decision.update(overrides)
    return decision


def test_old_preparation_and_lifecycle_messages_are_suppressed():
    messages = [
        "🎯 İŞLEM HAZIRLIĞI | AAVEUSDT\n🟢 LONG",
        "🎯 FIRSAT YAKALANDI – BEKLE\nİŞLEM DEĞİL | AAVEUSDT",
        "❌ GİRİŞİ KOVALAMA | AAVEUSDT",
        "🟡 KIRILIM HAZIRLIĞI | AAVEUSDT",
        "🚨 ERKEN HAREKET | AAVEUSDT",
        "🧭 2H SWING HAZIRLIĞI | AAVEUSDT",
        "🔄 YÖN DEĞİŞİMİ HAZIRLIĞI | AAVEUSDT",
        "⚫ AAVEUSDT | BİTTİ\nLONG",
        "🟢 AAVEUSDT | DEVAM EDİYOR\nLONG",
        "🟠 AAVEUSDT | GEÇ KALINDI\nLONG",
        "🟡 ERKEN HAREKET UYARISI — İŞLEM DEĞİL",
    ]
    assert all(simple.should_suppress(message) for message in messages)


def test_ordinary_preparation_stays_silent():
    text = simple.simple_preparation_message(_strong_plan(score=78))
    assert "FIRSAT YAKALANDI – BEKLE" in text
    assert simple.should_suppress(text) is True


def test_strong_preparation_becomes_early_entry_alert():
    plan = _strong_plan()
    assert simple.early_entry_eligible(plan) is True
    text = simple.simple_preparation_message(plan)
    assert "🎯 FIRSAT YAKALANDI – 🟠 ERKEN GİRİŞ UYGUN" in text
    assert "AAVEUSDT" in text
    assert "LONG" in text
    assert "Erken giriş skoru: 86" in text
    assert "Plan SL:" in text
    assert "İlk hedef:" in text
    assert "Tam 5M teyidi henüz yok" in text
    assert simple.should_suppress(text) is False


def test_early_entry_rejects_opposite_5m_or_market_direction():
    assert simple.early_entry_eligible(_strong_plan(structure_5m="SHORT")) is False
    assert simple.early_entry_eligible(_strong_plan(market_preferred_direction="SHORT")) is False


def test_early_entry_rejects_weak_geometry_or_volume():
    assert simple.early_entry_eligible(_strong_plan(zone_distance_percent=0.31)) is False
    assert simple.early_entry_eligible(_strong_plan(volume_ratio_5m=0.49)) is False
    assert simple.early_entry_eligible(_strong_plan(volume_ratio_15m=0.49)) is False
    assert simple.early_entry_eligible(_strong_plan(risk_percent=1.36)) is False
    assert simple.early_entry_eligible(_strong_plan(room_r=1.49)) is False
    assert simple.early_entry_eligible(_strong_plan(extension_atr_5m=1.26)) is False


def test_link_like_raw_early_move_becomes_orange_alert():
    decision = _strong_early()
    assert simple.early_move_eligible(decision) is True
    original = "🚨 ERKEN HAREKET | LINKUSDT\n🟢 LONG\n⚠️ İşlem teyidi değildir."
    text = simple.simple_early_move_message(decision, original)
    assert "🎯 FIRSAT YAKALANDI – 🟠 ERKEN GİRİŞ UYGUN" in text
    assert "LINKUSDT" in text
    assert "13.288" in text
    assert "3dk +0.28%" in text
    assert "5dk +0.43%" in text
    assert "Hacim: 1.25x" in text
    assert "Kaynak: erken hareket + 15M/1H yön uyumu" in text
    assert simple.should_suppress(text) is False


def test_weak_or_conflicted_raw_early_move_stays_silent():
    original = "🚨 ERKEN HAREKET | LINKUSDT\n🟢 LONG\n⚠️ İşlem teyidi değildir."
    for decision in [
        _strong_early(score=69),
        _strong_early(structure_5m="SHORT"),
        _strong_early(structure_15m="SHORT"),
        _strong_early(structure_1h="SHORT"),
        _strong_early(volume_ratio_1m=0.79),
        _strong_early(move_3m_percent=0.14),
        _strong_early(move_5m_percent=0.19),
        _strong_early(move_5m_percent=1.81),
        _strong_early(extension_atr_5m=1.26),
        _strong_early(market_preferred_direction="SHORT"),
    ]:
        assert simple.early_move_eligible(decision) is False
        assert simple.simple_early_move_message(decision, original) == original
        assert simple.should_suppress(original) is True


def test_short_raw_early_move_is_symmetric():
    decision = _strong_early(
        symbol="XPLUSDT",
        direction="SHORT",
        move_3m_percent=-0.31,
        move_5m_percent=-0.55,
        structure_5m="NEUTRAL",
        structure_15m="SHORT",
        structure_1h="SHORT",
    )
    assert simple.early_move_eligible(decision) is True


def test_trade_results_are_not_suppressed():
    assert simple.should_suppress("❌ STOP OLDU\nCoin: AAVEUSDT") is False
    assert simple.should_suppress("✅ TP1 GELDİ\nCoin: AAVEUSDT") is False
    assert simple.should_suppress("✅ TP3 GELDİ\nCoin: AAVEUSDT") is False


def test_real_trade_message_is_prime_like_and_compact():
    text = simple.simple_trade_message({
        "symbol": "AAVEUSDT",
        "direction": "LONG",
        "entry": 130.5,
        "sl": 128.0,
        "tp1": 134.0,
        "tp2": 138.0,
        "tp3": 144.0,
        "market_label": "YUKARI",
        "derivatives_soft_score": 4,
    })
    assert "🚨 KRİPTO İŞLEM" in text
    assert "AAVEUSDT" in text
    assert "LONG" in text
    assert "Giriş:" in text
    assert "Stop:" in text
    assert "TP1:" in text and "TP2:" in text and "TP3:" in text
    assert "Piyasa:" not in text
    assert "Teyit:" not in text
