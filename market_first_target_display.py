"""Telegram target display overlay for explicit price expectations.

The technical target engine already estimates an expected move and a potential
percentage range. This presentation layer converts those percentages back into
actual price levels so LONG/SHORT alerts state clearly how high/low the move is
expected to reach. It changes presentation only; strategy, guards and trade
eligibility are untouched.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import market_first_simple_mode as simple_mode
import market_first_runner as runner

VERSION = "MARKET_FIRST_TARGET_DISPLAY_V2_EXPLICIT_PRICE_2026_09_07"
_INSTALLED = False


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _entry_price(item: Mapping[str, Any]) -> float:
    return (
        _sf(item.get("entry"))
        or _sf(item.get("current_price"))
        or _sf(item.get("ideal_entry"))
    )


def _price_from_move(direction: str, entry: float, percent: float) -> float:
    if entry <= 0 or percent <= 0:
        return 0.0
    sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    return entry * (1.0 + sign * percent / 100.0)


def explicit_target_lines(item: Mapping[str, Any]) -> str:
    target = _sf(item.get("technical_target"))
    expected = _sf(item.get("expected_move_percent"))
    entry = _entry_price(item)
    direction = str(item.get("direction") or "").upper()
    if target <= 0 or expected <= 0 or entry <= 0 or direction not in {"LONG", "SHORT"}:
        return ""

    low = _sf(item.get("expected_move_low_percent"))
    high = _sf(item.get("expected_move_high_percent"))
    confidence = str(item.get("target_confidence") or "TEMKİNLİ")
    source = str(item.get("technical_target_source") or "teknik yapı")

    near_price = _price_from_move(direction, entry, low)
    far_price = _price_from_move(direction, entry, high)

    if direction == "LONG":
        expected_label = "Tahmini çıkabileceği ana seviye"
        band_label = "Beklenen yükseliş bölgesi"
        far_label = "Güçlü senaryoda çıkabileceği seviye"
        movement = "yükseliş"
    else:
        expected_label = "Tahmini düşebileceği ana seviye"
        band_label = "Beklenen düşüş bölgesi"
        far_label = "Güçlü senaryoda düşebileceği seviye"
        movement = "düşüş"

    lines = [
        f"\n🎯 {expected_label}: {runner.bot.format_price(target)}",
        f"\n📐 Beklenen {movement}: ~%{expected:.2f}",
    ]

    if near_price > 0 and far_price > 0 and high >= low > 0:
        lines.append(
            f"\n📍 {band_label}: "
            f"{runner.bot.format_price(near_price)} → {runner.bot.format_price(far_price)}"
        )
        lines.append(
            f"\n🚀 {far_label}: {runner.bot.format_price(far_price)}"
        )

    lines.append(f"\n🧭 Hedef kaynağı: {source} | Güven: {confidence}")
    return "".join(lines)


def install_target_display() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    simple_mode._target_lines = explicit_target_lines


def summary() -> dict:
    return {
        "version": VERSION,
        "telegram_target_display": "EXPLICIT_EXPECTED_PRICE_AND_PRICE_RANGE",
        "changes_strategy": False,
        "changes_guards": False,
    }
