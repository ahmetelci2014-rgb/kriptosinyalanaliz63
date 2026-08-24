from __future__ import annotations

import live_entry_safety as safety


EARLY_ELITE = """✅ İŞLEM GİRİŞİ — PREMIUM ERKEN HAREKET
━━━━━━━━━━━━━━━━━━
🟢 LONG | ONTUSDT
⚡ Yapı: TRIGGER • V2 100/100
💰 Giriş: 0.048260
🎯 TP1: 0.048500
🎯 TP2: 0.048700
🎯 TP3: 0.049000
🛑 SL: 0.047900

⭐ Premium skor: 100/100 • A+ ERKEN BREAKOUT
📊 5M hacim: 2.10x
🧬 Order Flow: —
📍 Anchor sapması: %0.10
🛡 Stop: %0.75
🔧 2x | Isolated
🛡 Portfolio: ALLOW

Not: uzun açıklama.
"""

EARLY_NON_ELITE = EARLY_ELITE.replace(
    "TRIGGER • V2 100/100", "TRIGGER • V2 91/100"
).replace(
    "Order Flow: —", "Order Flow: ✅ 65/100"
)


def test_compact_premium_keeps_only_trade_levels():
    compact = safety._compact_premium_entry(EARLY_ELITE)
    assert compact.splitlines() == [
        "✅ İŞLEM GİRİŞİ — PREMIUM ERKEN HAREKET",
        "🟢 LONG | ONTUSDT",
        "💰 Giriş: 0.048260",
        "🎯 TP1: 0.048500",
        "🎯 TP2: 0.048700",
        "🎯 TP3: 0.049000",
        "🛑 SL: 0.047900",
    ]
    assert "Premium skor" not in compact
    assert "Order Flow" not in compact
    assert "İŞLEM DİSİPLİNİ" not in compact


def test_elite_trigger_gets_immediate_policy():
    assert safety._elite_early_fast_send(EARLY_ELITE) is True


def test_lower_trigger_is_deferred_from_fast_slot():
    assert safety._elite_early_fast_send(EARLY_NON_ELITE) is False


def test_confirmed_flow_can_make_armed_elite():
    text = EARLY_ELITE.replace(
        "TRIGGER • V2 100/100", "ARMED • V2 86/100"
    ).replace(
        "Order Flow: —", "Order Flow: ✅ 91/100"
    ).replace(
        "Premium skor: 100/100", "Premium skor: 98/100"
    )
    assert safety._elite_early_fast_send(text) is True


def test_non_elite_fast_attempt_falls_back_but_batch_can_send():
    sent = []

    def sink(message, *args, **kwargs):
        sent.append(str(message))
        return True

    sender = safety.make_entry_safety_sender(sink)

    def _try_fast_send():
        return sender(EARLY_NON_ELITE)

    # First intra-scan attempt is deliberately deferred.
    assert _try_fast_send() is False
    assert sent == []

    # Normal end-of-scan selection is allowed and compacted.
    assert sender(EARLY_NON_ELITE) is True
    assert len(sent) == 1
    assert "Premium skor" not in sent[0]
    assert "🎯 TP3:" in sent[0]
