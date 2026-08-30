"""Live-main hooks for Market First pre-breakout ignition scouting.

Imported by market_first_new_listings before market_first_live captures the runner
functions. This keeps one live strategy while letting the ordinary Market First
pipeline inspect quieter, liquid coins that are only beginning to press a range
edge.
"""
from __future__ import annotations

import math
import os
from typing import Any, Mapping

import market_first_audit_layer as audit
import market_first_ignition as ignition
import market_first_runner as runner

_INSTALLED = False
IGNITION_SCOUT_SLOTS = 12
IGNITION_SCOUT_MIN_MOVE = 0.03
IGNITION_SCOUT_MAX_MOVE = 0.45
IGNITION_SCOUT_MIN_VOLUME = 750_000.0


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _scout_priority(row: Mapping[str, Any], sample_moves: Mapping[str, float]) -> float:
    symbol = str(row.get("symbol") or "")
    move = _sf(sample_moves.get(symbol))
    abs_move = abs(move)
    volume = _sf(row.get("quote_volume"))
    daily = _sf(row.get("change_24h"))
    if not (IGNITION_SCOUT_MIN_MOVE <= abs_move <= IGNITION_SCOUT_MAX_MOVE):
        return -1.0
    if volume < IGNITION_SCOUT_MIN_VOLUME:
        return -1.0
    aligned = 1.0 if move * daily > 0 else 0.0
    liquidity = min(3.0, math.log10(max(1.0, volume / IGNITION_SCOUT_MIN_VOLUME)))
    # Quieter first movement is useful, but avoid ranking pure zero-noise. Daily
    # alignment and liquidity break ties without assuming the daily trend must win.
    return abs_move * 6.0 + liquidity + aligned * 0.75


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_analyze = runner.analyze_candidate
    original_select = audit.select_deep_scan
    original_format_early = runner._format_early_message

    def analyze_with_ignition(*args, **kwargs):
        decision, reason = original_analyze(*args, **kwargs)
        if decision is not None:
            return decision, reason

        def arg(name: str, index: int, default=None):
            if name in kwargs:
                return kwargs.get(name)
            return args[index] if len(args) > index else default

        rescued, rescued_reason, diag = ignition.detect_ignition(
            decision,
            reason,
            symbol=str(arg("symbol", 0, "") or ""),
            df1m=arg("df1m", 1),
            df5m=arg("df5m", 2),
            df15m=arg("df15m", 3),
            df1h=arg("df1h", 4),
            current_price=float(arg("current_price", 5, 0.0) or 0.0),
            quote_volume_24h=float(arg("quote_volume_24h", 6, 0.0) or 0.0),
            context=arg("context", 7),
        )
        if diag.get("promoted"):
            print(
                "IGNITION -> ERKEN:",
                rescued.get("symbol"),
                rescued.get("direction"),
                "| skor=", rescued.get("score"),
                "| sıkışma=", rescued.get("compression_ratio_5m"),
                "| kenara=", rescued.get("distance_to_breakout_percent"),
                "| hacim3=", rescued.get("volume_ratio_3m"),
            )
        return rescued, rescued_reason

    def select_with_ignition_scouts(rows, sample_moves, state, original_selector):
        base = original_select(rows, sample_moves, state, original_selector)
        ranked = []
        for row in rows:
            priority = _scout_priority(row, sample_moves)
            if priority >= 0:
                ranked.append((priority, str(row.get("symbol") or "")))
        scouts = [symbol for _, symbol in sorted(ranked, reverse=True)[:IGNITION_SCOUT_SLOTS]]
        merged = []
        seen = set()
        for symbol in [*scouts, *base]:
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            merged.append(symbol)
            if len(merged) >= audit.MAX_AUDITED_DEEP_SCAN:
                break
        if scouts:
            print("MARKET FIRST IGNITION SCOUTS:", scouts[:IGNITION_SCOUT_SLOTS])
        return merged

    def format_early_with_ignition(decision):
        if not bool((decision or {}).get("ignition_setup")):
            return original_format_early(decision)
        direction = str(decision.get("direction") or "")
        icon = "🟢" if direction == "LONG" else "🔴"
        market = str(decision.get("market_label") or "KARIŞIK")
        distance = float(decision.get("distance_to_breakout_percent") or 0.0)
        volume3 = float(decision.get("volume_ratio_3m") or 0.0)
        return (
            f"🟡 KIRILIM HAZIRLIĞI | {decision.get('symbol')}\n"
            f"{icon} {direction} | Piyasa: {market}\n"
            f"📏 Seviyeye uzaklık: %{distance:.2f}\n"
            f"🔊 3dk hacim: {volume3:.2f}x\n"
            f"✅ Durum: ERKEN\n"
            f"⚠️ Henüz işlem teyidi değildir."
        )

    runner.analyze_candidate = analyze_with_ignition
    audit.select_deep_scan = select_with_ignition_scouts
    runner._format_early_message = format_early_with_ignition


if str(os.getenv("GITHUB_REF_NAME") or "").strip() == "main":
    install()
