"""Simple Telegram presentation for the single Market First live system.

The strategy keeps every internal preparation, early-alert, swing and direction
ledger. Telegram intentionally exposes only the two useful decision points:
- one compact, high-quality preparation alert (FIRSAT YAKALANDI),
- real trade entries and TP/SL/BE lifecycle results.

Lower-confidence early movement, breakout, chased, swing and lifecycle-noise
messages remain silent. No signal score, direction, entry, stop, target, risk or
portfolio rule changes.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import market_first_entry_plan as entry_plan
import market_first_runner as runner

VERSION = "MARKET_FIRST_SIMPLE_TELEGRAM_V2_2026_09_06"
_INSTALLED = False

# Keep the old verbose preparation prefix blocked as a safety fallback. During
# install we replace entry_plan.format_preparation with the compact formatter
# below, whose FIRSAT YAKALANDI prefix is intentionally allowed through.
SUPPRESSED_PREFIXES = (
    "🎯 İŞLEM HAZIRLIĞI",
    "❌ GİRİŞİ KOVALAMA",
    "🟡 KIRILIM HAZIRLIĞI",
    "🚨 ERKEN HAREKET",
    "🧭 2H SWING HAZIRLIĞI",
    "🔄 YÖN DEĞİŞİMİ HAZIRLIĞI",
    "🟡 ERKEN HAREKET UYARISI",
    "🟠 YAPI TEYİDİ",
)

SUPPRESSED_MARKERS = (
    " | DEVAM EDİYOR\n",
    " | GEÇ KALINDI\n",
    " | BİTTİ\n",
    "İŞLEM DEĞİL",
    "İşlem teyidi değildir",
)


def should_suppress(text: Any) -> bool:
    message = str(text or "").strip()
    if not message:
        return False
    if any(message.startswith(prefix) for prefix in SUPPRESSED_PREFIXES):
        return True
    return any(marker in message for marker in SUPPRESSED_MARKERS)


def simple_preparation_message(plan: Mapping[str, Any]) -> str:
    """Compact high-quality preparation alert; explicitly not a trade entry."""
    direction = str(plan.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    try:
        score = int(float(plan.get("score") or 0))
    except Exception:
        score = 0
    return (
        f"🎯 FIRSAT YAKALANDI\n\n"
        f"🪙 Parite: {plan.get('symbol')}\n"
        f"📊 Yön: {icon} {direction}\n"
        f"💵 Fiyat: {runner.bot.format_price(plan.get('current_price'))}\n"
        f"📍 İzlenen bölge: "
        f"{runner.bot.format_price(plan.get('zone_low'))} - "
        f"{runner.bot.format_price(plan.get('zone_high'))}\n"
        f"⭐ Hazırlık skoru: {score}\n"
        f"⏳ Henüz işlem değil; giriş teyidi bekleniyor."
    )


def simple_trade_message(signal: Mapping[str, Any]) -> str:
    direction = str(signal.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    return (
        f"🚨 KRİPTO İŞLEM\n\n"
        f"🪙 Parite: {signal.get('symbol')}\n"
        f"📊 Yön: {icon} {direction}\n\n"
        f"📍 Giriş: {runner.bot.format_price(signal.get('entry'))}\n"
        f"🛑 Stop: {runner.bot.format_price(signal.get('sl'))}\n"
        f"🎯 TP1: {runner.bot.format_price(signal.get('tp1'))}\n"
        f"🎯 TP2: {runner.bot.format_price(signal.get('tp2'))}\n"
        f"🎯 TP3: {runner.bot.format_price(signal.get('tp3'))}"
    )


def install_simple_mode() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_send = runner._send

    def simple_send(text: str, delivery_key: Optional[str] = None) -> bool:
        if should_suppress(text):
            print("TELEGRAM SIMPLE MODE | sessiz takip:", str(text).splitlines()[0])
            # False intentionally means "not delivered to Telegram". The live
            # observational ledgers are maintained independently of Telegram.
            return False
        return original_send(text, delivery_key=delivery_key)

    # Preserve the proven preparation engine and its existing score/cooldown.
    # Only its Telegram presentation changes from verbose to one compact alert.
    entry_plan.format_preparation = simple_preparation_message
    runner._send = simple_send
    runner._format_trade_message = simple_trade_message


def summary() -> dict:
    return {
        "version": VERSION,
        "telegram_mode": "QUALITY_PREP_TRADE_AND_RESULTS",
        "preparations": "COMPACT_TELEGRAM_PLUS_INTERNAL_LEDGER",
        "other_observations": "INTERNAL_LEDGER_ONLY",
    }
