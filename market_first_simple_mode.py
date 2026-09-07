"""Simple Telegram presentation for the single Market First live system.

All preparation, early-alert, swing and direction ledgers keep running internally.
Telegram intentionally exposes only the useful decision points:
- selective EARLY ENTRY alerts from already-qualified entry-plan preparations,
- real trade entries and TP/SL/BE lifecycle results.

Ordinary PREP/BEKLE, lower-confidence early movement, breakout, chased, swing and
lifecycle-noise messages remain silent. This module changes presentation only;
it does not promote a PREP into a real trade and it does not bypass any live guard.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

import market_first_entry_plan as entry_plan
import market_first_runner as runner

VERSION = "MARKET_FIRST_SIMPLE_TELEGRAM_V3_EARLY_ENTRY_2026_09_07"
_INSTALLED = False

# A PREP must clear this narrower quality gate before it is worth interrupting the
# user on Telegram. Normal PREP still stays in the existing opportunity ledger.
EARLY_ENTRY_MIN_SCORE = 82
EARLY_ENTRY_MAX_ZONE_DISTANCE_PERCENT = 0.30
EARLY_ENTRY_MIN_VOLUME_RATIO_5M = 0.50
EARLY_ENTRY_MIN_VOLUME_RATIO_15M = 0.50
EARLY_ENTRY_MAX_RISK_PERCENT = 1.35
EARLY_ENTRY_MIN_ROOM_R = 1.50
EARLY_ENTRY_MAX_EXTENSION_ATR_5M = 1.25

# Keep the old verbose preparation prefix and all lower-confidence stages blocked.
SUPPRESSED_PREFIXES = (
    "🎯 İŞLEM HAZIRLIĞI",
    "🎯 FIRSAT YAKALANDI – BEKLE",
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


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def should_suppress(text: Any) -> bool:
    message = str(text or "").strip()
    if not message:
        return False
    if any(message.startswith(prefix) for prefix in SUPPRESSED_PREFIXES):
        return True
    return any(marker in message for marker in SUPPRESSED_MARKERS)


def early_entry_eligible(plan: Mapping[str, Any]) -> bool:
    """Return True only for a strong PREP that is close enough to act early.

    Full 5M confirmation is intentionally *not* required here. 5M may be aligned
    or neutral, but it may not point against the 15M+1H plan. A True result is
    still observational/early-entry guidance, never an automatic trade promotion.
    """
    direction = str(plan.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False

    if int(_sf(plan.get("score"))) < EARLY_ENTRY_MIN_SCORE:
        return False
    if _sf(plan.get("zone_distance_percent"), 999.0) > EARLY_ENTRY_MAX_ZONE_DISTANCE_PERCENT:
        return False
    if _sf(plan.get("volume_ratio_5m")) < EARLY_ENTRY_MIN_VOLUME_RATIO_5M:
        return False
    if _sf(plan.get("volume_ratio_15m")) < EARLY_ENTRY_MIN_VOLUME_RATIO_15M:
        return False

    risk_percent = _sf(plan.get("risk_percent"), 999.0)
    if risk_percent <= 0 or risk_percent > EARLY_ENTRY_MAX_RISK_PERCENT:
        return False
    if _sf(plan.get("room_r")) < EARLY_ENTRY_MIN_ROOM_R:
        return False
    if _sf(plan.get("extension_atr_5m"), 999.0) > EARLY_ENTRY_MAX_EXTENSION_ATR_5M:
        return False

    if str(plan.get("structure_15m") or "").upper() != direction:
        return False
    if str(plan.get("structure_1h") or "").upper() != direction:
        return False

    opposite = "SHORT" if direction == "LONG" else "LONG"
    micro = str(plan.get("structure_5m") or "").upper()
    if micro not in {direction, "NEUTRAL"}:
        return False

    preferred = str(plan.get("market_preferred_direction") or "").upper()
    if preferred == opposite:
        return False
    return True


def simple_preparation_message(plan: Mapping[str, Any]) -> str:
    """Send only selective early-entry PREPs; keep ordinary PREPs silent."""
    direction = str(plan.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    score = int(_sf(plan.get("score")))

    if not early_entry_eligible(plan):
        # The normal send hook suppresses this marker. Returning a silent marker
        # instead of changing the entry-plan engine preserves all existing ledger
        # registration and lets a later stronger PREP be reconsidered normally.
        return (
            f"🎯 FIRSAT YAKALANDI – BEKLE\n"
            f"İŞLEM DEĞİL | {plan.get('symbol')} | {direction} | skor={score}"
        )

    return (
        f"🎯 FIRSAT YAKALANDI – 🟠 ERKEN GİRİŞ UYGUN\n\n"
        f"🪙 Parite: {plan.get('symbol')}\n"
        f"📊 Yön: {icon} {direction}\n"
        f"💵 Fiyat: {runner.bot.format_price(plan.get('current_price'))}\n"
        f"📍 Erken giriş bölgesi: "
        f"{runner.bot.format_price(plan.get('zone_low'))} - "
        f"{runner.bot.format_price(plan.get('zone_high'))}\n"
        f"🛑 Plan SL: {runner.bot.format_price(plan.get('sl'))}\n"
        f"🎯 İlk hedef: {runner.bot.format_price(plan.get('tp1'))}\n"
        f"⭐ Erken giriş skoru: {score}\n"
        f"⚠️ Tam 5M teyidi henüz yok; erken giriş daha risklidir."
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
            # False means "not delivered to Telegram". Observational ledgers are
            # maintained independently by the tracking wrappers.
            return False
        return original_send(text, delivery_key=delivery_key)

    # Preserve the proven preparation engine and its existing cooldown/state.
    # Only the Telegram presentation/filter is changed here.
    entry_plan.format_preparation = simple_preparation_message
    runner._send = simple_send
    runner._format_trade_message = simple_trade_message


def summary() -> dict:
    return {
        "version": VERSION,
        "telegram_mode": "SELECTIVE_EARLY_ENTRY_TRADE_AND_RESULTS",
        "ordinary_preparations": "INTERNAL_LEDGER_ONLY",
        "early_entry_preparations": "SELECTIVE_TELEGRAM_PLUS_INTERNAL_LEDGER",
        "other_observations": "INTERNAL_LEDGER_ONLY",
    }
