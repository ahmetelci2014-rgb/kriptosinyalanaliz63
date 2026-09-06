import market_first_simple_mode as simple


def test_preparation_and_lifecycle_messages_are_suppressed():
    messages = [
        "🎯 İŞLEM HAZIRLIĞI | AAVEUSDT\n🟢 LONG",
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
