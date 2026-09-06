"""Prime-like Telegram presentation for the single Market First live system.

The strategy keeps every internal preparation, early-alert, swing and direction
ledger.  This module changes only what reaches the user's Telegram:
- suppress observational/preparation/lifecycle noise,
- keep real trade entries and TP/SL/BE lifecycle results,
- present real entries in one compact format.

No signal score, direction, entry, stop, target, risk or portfolio rule changes.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import market_first_runner as runner

VERSION = "MARKET_FIRST_SIMPLE_TELEGRAM_V1_2026_09_06"
_INSTALLED = False

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

    runner._send = simple_send
    runner._format_trade_message = simple_trade_message


def summary() -> dict:
    return {
        "version": VERSION,
        "telegram_mode": "TRADE_AND_RESULTS_ONLY",
        "preparations": "INTERNAL_LEDGER_ONLY",
    }
