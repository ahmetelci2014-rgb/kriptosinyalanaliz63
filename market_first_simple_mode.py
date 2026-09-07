"""Simple Telegram presentation for the single Market First live system.

All preparation, early-alert, swing and direction ledgers keep running internally.
Telegram exposes only useful decision points and now includes a technical target
plan at message time:
- selective EARLY ENTRY alerts from qualified entry-plan preparations,
- selective EARLY-MOVE alerts with enough structure/momentum quality,
- real trade entries and TP/SL/BE lifecycle results.

Ordinary PREP/BEKLE, lower-confidence early movement, breakout, chased, swing and
lifecycle-noise messages remain silent. Presentation/target guidance never promotes
an alert into a trade and never bypasses a live guard.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

import market_first_entry_plan as entry_plan
import market_first_runner as runner

VERSION = "MARKET_FIRST_SIMPLE_TELEGRAM_V5_TECHNICAL_TARGET_2026_09_07"
_INSTALLED = False

# Qualified PREP -> selective Telegram early-entry alert.
EARLY_ENTRY_MIN_SCORE = 82
EARLY_ENTRY_MAX_ZONE_DISTANCE_PERCENT = 0.30
EARLY_ENTRY_MIN_VOLUME_RATIO_5M = 0.50
EARLY_ENTRY_MIN_VOLUME_RATIO_15M = 0.50
EARLY_ENTRY_MAX_RISK_PERCENT = 1.35
EARLY_ENTRY_MIN_ROOM_R = 1.50
EARLY_ENTRY_MAX_EXTENSION_ATR_5M = 1.25

# EARLY movement -> selective Telegram bridge. This deliberately sits below the
# real trade threshold: it is an earlier heads-up, not an automatic promotion.
EARLY_MOVE_MIN_SCORE = 70
EARLY_MOVE_MIN_VOLUME_RATIO_1M = 0.80
EARLY_MOVE_MIN_ALIGNED_MOVE_3M = 0.15
EARLY_MOVE_MIN_ALIGNED_MOVE_5M = 0.20
EARLY_MOVE_MAX_ALIGNED_MOVE_5M = 1.80
EARLY_MOVE_MAX_EXTENSION_ATR_5M = 1.25
EARLY_MOVE_MIN_RELATIVE_STRENGTH_5M = 0.00

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


def _aligned_percent(direction: str, value: Any) -> float:
    raw = _sf(value)
    return raw if str(direction).upper() == "LONG" else -raw


def _reference(item: Mapping[str, Any], key: str) -> float:
    return _sf(item.get(key)) or _sf(item.get(f"target_reference_{key}"))


def _target_lines(item: Mapping[str, Any]) -> str:
    target = _sf(item.get("technical_target"))
    expected = _sf(item.get("expected_move_percent"))
    if target <= 0 or expected <= 0:
        return ""

    direction = str(item.get("direction") or "").upper()
    movement = "yükseliş" if direction == "LONG" else "düşüş"
    low = _sf(item.get("expected_move_low_percent"))
    high = _sf(item.get("expected_move_high_percent"))
    confidence = str(item.get("target_confidence") or "TEMKİNLİ")
    source = str(item.get("technical_target_source") or "teknik yapı")

    range_line = ""
    if low > 0 and high >= low:
        range_line = f"\n📏 Potansiyel aralık: %{low:.2f} - %{high:.2f}"
    return (
        f"\n🎯 ANA HEDEF: {runner.bot.format_price(target)}"
        f"\n📐 Beklenen {movement}: ~%{expected:.2f}"
        f"{range_line}"
        f"\n🧭 Hedef: {source} | Güven: {confidence}"
    )


def should_suppress(text: Any) -> bool:
    message = str(text or "").strip()
    if not message:
        return False
    if any(message.startswith(prefix) for prefix in SUPPRESSED_PREFIXES):
        return True
    return any(marker in message for marker in SUPPRESSED_MARKERS)


def early_entry_eligible(plan: Mapping[str, Any]) -> bool:
    """Return True only for a strong PREP that is close enough to act early."""
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


def early_move_eligible(decision: Mapping[str, Any]) -> bool:
    """Promote only high-quality raw EARLY observations to Telegram visibility."""
    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False
    if str(decision.get("stage") or "").upper() != "EARLY":
        return False
    if int(_sf(decision.get("score"))) < EARLY_MOVE_MIN_SCORE:
        return False

    if str(decision.get("structure_15m") or "").upper() != direction:
        return False
    if str(decision.get("structure_1h") or "").upper() != direction:
        return False

    micro = str(decision.get("structure_5m") or "").upper()
    if micro not in {direction, "NEUTRAL"}:
        return False

    if _sf(decision.get("volume_ratio_1m")) < EARLY_MOVE_MIN_VOLUME_RATIO_1M:
        return False
    if _sf(decision.get("extension_atr_5m"), 999.0) > EARLY_MOVE_MAX_EXTENSION_ATR_5M:
        return False

    move3 = _aligned_percent(direction, decision.get("move_3m_percent"))
    move5 = _aligned_percent(direction, decision.get("move_5m_percent"))
    if move3 < EARLY_MOVE_MIN_ALIGNED_MOVE_3M:
        return False
    if move5 < EARLY_MOVE_MIN_ALIGNED_MOVE_5M or move5 > EARLY_MOVE_MAX_ALIGNED_MOVE_5M:
        return False
    if _sf(decision.get("relative_strength_5m")) < EARLY_MOVE_MIN_RELATIVE_STRENGTH_5M:
        return False

    opposite = "SHORT" if direction == "LONG" else "LONG"
    preferred = str(decision.get("market_preferred_direction") or "").upper()
    if preferred == opposite:
        return False
    return True


def simple_preparation_message(plan: Mapping[str, Any]) -> str:
    """Send only selective early-entry PREPs; keep ordinary PREPs silent."""
    direction = str(plan.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    score = int(_sf(plan.get("score")))

    if not early_entry_eligible(plan):
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
        f"🛑 Plan SL: {runner.bot.format_price(_reference(plan, 'sl'))}\n"
        f"🎯 TP1: {runner.bot.format_price(_reference(plan, 'tp1'))}\n"
        f"🎯 TP2: {runner.bot.format_price(_reference(plan, 'tp2'))}\n"
        f"🎯 TP3: {runner.bot.format_price(_reference(plan, 'tp3'))}"
        f"{_target_lines(plan)}\n"
        f"⭐ Erken giriş skoru: {score}\n"
        f"⚠️ Teknik hedef tahmindir; tam 5M teyidi henüz yok."
    )


def simple_early_move_message(decision: Mapping[str, Any], original_text: str) -> str:
    """Expose only strong raw EARLY observations; leave the rest suppressible."""
    if not early_move_eligible(decision):
        return original_text

    direction = str(decision.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    score = int(_sf(decision.get("score")))
    move3 = _aligned_percent(direction, decision.get("move_3m_percent"))
    move5 = _aligned_percent(direction, decision.get("move_5m_percent"))
    return (
        f"🎯 FIRSAT YAKALANDI – 🟠 ERKEN GİRİŞ UYGUN\n\n"
        f"🪙 Parite: {decision.get('symbol')}\n"
        f"📊 Yön: {icon} {direction}\n"
        f"💵 Fiyat: {runner.bot.format_price(decision.get('current_price'))}\n"
        f"🛑 Plan SL: {runner.bot.format_price(_reference(decision, 'sl'))}\n"
        f"🎯 TP1: {runner.bot.format_price(_reference(decision, 'tp1'))}\n"
        f"🎯 TP2: {runner.bot.format_price(_reference(decision, 'tp2'))}\n"
        f"🎯 TP3: {runner.bot.format_price(_reference(decision, 'tp3'))}"
        f"{_target_lines(decision)}\n"
        f"⚡ Erken hareket: 3dk +{move3:.2f}% | 5dk +{move5:.2f}%\n"
        f"🔊 Hacim: {_sf(decision.get('volume_ratio_1m')):.2f}x\n"
        f"⭐ Erken giriş skoru: {score}\n"
        f"⚠️ Teknik hedef tahmindir; tam işlem teyidi değildir."
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
        f"{_target_lines(signal)}\n"
        f"ℹ️ Ana hedef ve yüzde hareket teknik tahmindir; garanti değildir."
    )


def install_simple_mode() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_send = runner._send
    original_early_formatter = runner._format_early_message

    def simple_send(text: str, delivery_key: Optional[str] = None) -> bool:
        if should_suppress(text):
            print("TELEGRAM SIMPLE MODE | sessiz takip:", str(text).splitlines()[0])
            return False
        return original_send(text, delivery_key=delivery_key)

    def selective_early_formatter(decision: Mapping[str, Any]) -> str:
        original_text = original_early_formatter(decision)
        return simple_early_move_message(decision, original_text)

    entry_plan.format_preparation = simple_preparation_message
    runner._format_early_message = selective_early_formatter
    runner._send = simple_send
    runner._format_trade_message = simple_trade_message


def summary() -> dict:
    return {
        "version": VERSION,
        "telegram_mode": "SELECTIVE_EARLY_TARGET_TRADE_AND_RESULTS",
        "ordinary_preparations": "INTERNAL_LEDGER_ONLY",
        "qualified_entry_preparations": "SELECTIVE_TELEGRAM_WITH_TARGET",
        "qualified_early_moves": "SELECTIVE_TELEGRAM_WITH_TARGET",
        "real_trades": "TELEGRAM_WITH_TECHNICAL_TARGET_AND_EXPECTED_PERCENT",
        "other_observations": "INTERNAL_LEDGER_ONLY",
    }
