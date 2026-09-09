"""Market First Profit Quality V1.

Single-entry Telegram mode + profit-potential gate.

Goals:
- keep PREP/EARLY/Big-Move observations internal,
- send only one final trade entry message per opportunity,
- reject otherwise-valid trades whose closed-candle 15M/1H/2H structure does not
  offer a meaningful directional move,
- re-anchor TP1/TP2/TP3 inside that structural move instead of preserving the
  old small fixed-R ladder.

This module never places exchange orders and does not bypass existing Market
First safety, duplicate, portfolio, market or cooldown guards.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Optional, Tuple

import market_first_runner as runner
import market_first_simple_mode as simple_mode
import market_first_strategy as strategy
import market_first_target_engine as target_engine

VERSION = "MARKET_FIRST_PROFIT_QUALITY_V1_2026_09_09"
MODE = "SINGLE_FINAL_TRADE_TELEGRAM_HIGHER_PROFIT_POTENTIAL"
STATE_FILE = "market_first_profit_quality.json"

# Quality first: a final trade must be strong AND have meaningful structural room.
MIN_SCORE = 88
MIN_EXPECTED_MOVE_PERCENT = 2.00
MIN_TARGET_R = 2.50
MAX_RISK_PERCENT = 1.25
MIN_VOLUME_RATIO_5M = 0.65
MAX_TARGET_MOVE_PERCENT = 5.00

# Profit ladder is carved out of the selected structural target.
TP1_FRACTION = 0.50
TP2_FRACTION = 0.75
TP3_FRACTION = 1.00

_ALLOWED_CONFIDENCE = {"ORTA", "YÜKSEK"}
_INSTALLED = False
_RUN_COUNTS: Counter = Counter()
_RUN_ACCEPTED: list[Dict[str, Any]] = []


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _arg(args, kwargs, name: str, index: int, default=None):
    if name in kwargs:
        return kwargs.get(name)
    return args[index] if len(args) > index else default


def _directional_percent(direction: str, entry: float, target: float) -> float:
    if min(entry, target) <= 0:
        return 0.0
    raw = (target / entry - 1.0) * 100.0
    return raw if str(direction).upper() == "LONG" else -raw


def _price_from_percent(direction: str, entry: float, percent: float) -> float:
    sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    return entry * (1.0 + sign * percent / 100.0)


def _profit_target(
    *,
    direction: str,
    entry: float,
    risk_percent: float,
    df15m: Any,
    df1h: Any,
) -> Optional[Dict[str, Any]]:
    """Pick the nearest closed-candle structural level that still offers >=2%."""
    if direction not in {"LONG", "SHORT"} or entry <= 0 or risk_percent <= 0:
        return None
    try:
        s15 = strategy._structure(df15m, entry)
        s1h = strategy._structure(df1h, entry)
        two = target_engine._two_hour_context(df1h, entry)
    except Exception:
        return None
    if not isinstance(s15, Mapping) or not isinstance(s1h, Mapping):
        return None

    # A high-profit final trade still requires both higher entry timeframes aligned.
    d15 = str(s15.get("direction") or "NEUTRAL").upper()
    d1h = str(s1h.get("direction") or "NEUTRAL").upper()
    if d15 != direction or d1h != direction:
        return None

    if direction == "LONG":
        raw_levels = [
            ("15M direnç", _sf(s15.get("range_high_72"))),
            ("1H direnç", _sf(s1h.get("range_high_72"))),
            ("2H direnç", _sf((two or {}).get("range_high"))),
        ]
    else:
        raw_levels = [
            ("15M destek", _sf(s15.get("range_low_72"))),
            ("1H destek", _sf(s1h.get("range_low_72"))),
            ("2H destek", _sf((two or {}).get("range_low"))),
        ]

    candidates = []
    for source, level in raw_levels:
        move_percent = _directional_percent(direction, entry, level)
        if move_percent < MIN_EXPECTED_MOVE_PERCENT:
            continue
        target_r = move_percent / risk_percent if risk_percent > 0 else 0.0
        if target_r < MIN_TARGET_R:
            continue
        candidates.append((move_percent, source, level, target_r))

    if not candidates:
        return None

    # Nearest qualifying structure is more realistic than blindly targeting the farthest.
    raw_move, source, raw_level, raw_r = min(candidates, key=lambda item: item[0])
    move_percent = min(raw_move, MAX_TARGET_MOVE_PERCENT)
    target = _price_from_percent(direction, entry, move_percent)
    target_r = move_percent / risk_percent if risk_percent > 0 else 0.0
    source_label = source if raw_move <= MAX_TARGET_MOVE_PERCENT else f"{source} • %{MAX_TARGET_MOVE_PERCENT:.0f} tavan"
    return {
        "profit_target": round(target, 12),
        "profit_target_percent": round(move_percent, 4),
        "profit_target_r": round(target_r, 3),
        "profit_target_source": source_label,
        "profit_raw_structure_level": round(raw_level, 12),
        "profit_raw_structure_percent": round(raw_move, 4),
        "profit_structure_15m": d15,
        "profit_structure_1h": d1h,
        "profit_structure_2h": str((two or {}).get("direction") or "NEUTRAL").upper(),
    }


def _quality_reason(decision: Mapping[str, Any]) -> Tuple[bool, str]:
    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, "DIRECTION"
    if int(_sf(decision.get("score"))) < MIN_SCORE:
        return False, "SCORE_BELOW_88"

    risk_percent = _sf(decision.get("risk_percent"), 999.0)
    if risk_percent <= 0 or risk_percent > MAX_RISK_PERCENT:
        return False, "RISK_ABOVE_1_25"

    volume5 = max(
        _sf(decision.get("volume_ratio_5m")),
        _sf(decision.get("volume_ratio_1m")),
        _sf(decision.get("volume_ratio")),
    )
    if volume5 < MIN_VOLUME_RATIO_5M:
        return False, "VOLUME_BELOW_0_65"

    move_percent = _sf(decision.get("profit_target_percent"))
    target_r = _sf(decision.get("profit_target_r"))
    if move_percent < MIN_EXPECTED_MOVE_PERCENT:
        return False, "EXPECTED_MOVE_BELOW_2P"
    if target_r < MIN_TARGET_R:
        return False, "TARGET_R_BELOW_2_5"

    preferred = str(decision.get("market_preferred_direction") or "").upper()
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if preferred == opposite:
        return False, "MARKET_OPPOSITE"

    # If target overlay supplied confidence, do not accept a cautious final trade.
    confidence = str(decision.get("target_confidence") or "").upper()
    if confidence and confidence not in _ALLOWED_CONFIDENCE:
        return False, "TARGET_CONFIDENCE_LOW"
    return True, "OK"


def _reanchor_signal(signal: Mapping[str, Any], decision: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(signal)
    direction = str(result.get("direction") or decision.get("direction") or "").upper()
    entry = _sf(result.get("entry")) or _sf(decision.get("current_price"))
    sl = _sf(result.get("sl")) or _sf(decision.get("sl"))
    target_percent = _sf(decision.get("profit_target_percent"))
    if direction not in {"LONG", "SHORT"} or min(entry, sl, target_percent) <= 0:
        return result

    tp1_percent = target_percent * TP1_FRACTION
    tp2_percent = target_percent * TP2_FRACTION
    tp3_percent = target_percent * TP3_FRACTION
    risk_percent = abs(entry - sl) / entry * 100.0 if entry > 0 else 0.0

    result["legacy_tp1"] = result.get("tp1")
    result["legacy_tp2"] = result.get("tp2")
    result["legacy_tp3"] = result.get("tp3")
    result["tp1"] = round(_price_from_percent(direction, entry, tp1_percent), 12)
    result["tp2"] = round(_price_from_percent(direction, entry, tp2_percent), 12)
    result["tp3"] = round(_price_from_percent(direction, entry, tp3_percent), 12)
    result["rr_tp1"] = round(tp1_percent / risk_percent, 3) if risk_percent > 0 else 0.0
    result["rr_tp2"] = round(tp2_percent / risk_percent, 3) if risk_percent > 0 else 0.0
    result["rr_tp3"] = round(tp3_percent / risk_percent, 3) if risk_percent > 0 else 0.0
    result["profit_quality_version"] = VERSION
    result["profit_target"] = decision.get("profit_target")
    result["profit_target_percent"] = round(target_percent, 4)
    result["profit_target_r"] = decision.get("profit_target_r")
    result["profit_target_source"] = decision.get("profit_target_source")
    result["technical_target"] = result["tp3"]
    result["technical_target_r"] = result["rr_tp3"]
    result["technical_target_source"] = decision.get("profit_target_source")
    result["expected_move_percent"] = round(tp3_percent, 3)
    result["expected_move_low_percent"] = round(tp1_percent, 3)
    result["expected_move_high_percent"] = round(tp3_percent, 3)
    result["target_confidence"] = "YÜKSEK" if int(_sf(decision.get("score"))) >= 92 else "ORTA"
    return result


def _trade_message(signal: Mapping[str, Any]) -> str:
    direction = str(signal.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    entry = _sf(signal.get("entry"))
    tp1p = _directional_percent(direction, entry, _sf(signal.get("tp1")))
    tp2p = _directional_percent(direction, entry, _sf(signal.get("tp2")))
    tp3p = _directional_percent(direction, entry, _sf(signal.get("tp3")))
    score = int(_sf(signal.get("score")))
    return (
        "🚨 KALİTELİ KRİPTO İŞLEM\n\n"
        f"🪙 Parite: {signal.get('symbol')}\n"
        f"📊 Yön: {icon} {direction}\n"
        f"⭐ Kalite skoru: {score}/100\n\n"
        f"📍 Giriş: {runner.bot.format_price(signal.get('entry'))}\n"
        f"🛑 Stop: {runner.bot.format_price(signal.get('sl'))}\n"
        f"🎯 TP1: {runner.bot.format_price(signal.get('tp1'))} (~%{tp1p:.2f})\n"
        f"🎯 TP2: {runner.bot.format_price(signal.get('tp2'))} (~%{tp2p:.2f})\n"
        f"🚀 TP3 / Ana hedef: {runner.bot.format_price(signal.get('tp3'))} (~%{tp3p:.2f})\n"
        f"🧭 Hedef kaynağı: {signal.get('profit_target_source') or signal.get('technical_target_source')}\n"
        f"📐 Hedef/R: {_sf(signal.get('rr_tp3')):.2f}R\n"
        "ℹ️ Tek nihai giriş mesajıdır; otomatik emir açılmaz."
    )


def _save_summary() -> Dict[str, Any]:
    payload = {
        "version": VERSION,
        "mode": MODE,
        "thresholds": {
            "min_score": MIN_SCORE,
            "min_expected_move_percent": MIN_EXPECTED_MOVE_PERCENT,
            "min_target_r": MIN_TARGET_R,
            "max_risk_percent": MAX_RISK_PERCENT,
            "min_volume_ratio_5m": MIN_VOLUME_RATIO_5M,
            "max_target_move_percent": MAX_TARGET_MOVE_PERCENT,
            "tp_fractions": [TP1_FRACTION, TP2_FRACTION, TP3_FRACTION],
        },
        "run_counts": dict(_RUN_COUNTS),
        "accepted": _RUN_ACCEPTED[-20:],
        "note": "Counts are signal-gate observations, not realized PnL.",
    }
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, prefix=".profit_quality.", suffix=".tmp", delete=False) as handle:
            temp_path = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, STATE_FILE)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
    return payload


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_analyze = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal
    original_send = runner._send

    def analyze_with_profit_target(*args, **kwargs):
        result = original_analyze(*args, **kwargs)
        try:
            decision, reason = result
        except Exception:
            return result
        if not isinstance(decision, Mapping):
            return result

        direction = str(decision.get("direction") or "").upper()
        entry = _sf(decision.get("current_price")) or _sf(_arg(args, kwargs, "current_price", 5, 0.0))
        risk_percent = _sf(decision.get("risk_percent"), 999.0)
        target = _profit_target(
            direction=direction,
            entry=entry,
            risk_percent=risk_percent,
            df15m=_arg(args, kwargs, "df15m", 3),
            df1h=_arg(args, kwargs, "df1h", 4),
        )
        enriched = dict(decision)
        if target:
            enriched.update(target)
        else:
            enriched["profit_target_percent"] = 0.0
            enriched["profit_target_r"] = 0.0
            enriched["profit_target_source"] = None
        return enriched, reason

    def decision_to_signal_profit_quality(decision):
        signal = original_decision_to_signal(decision)
        if not isinstance(signal, Mapping):
            return signal
        ok, reason = _quality_reason(decision if isinstance(decision, Mapping) else {})
        _RUN_COUNTS[reason] += 1
        if not ok:
            print("PROFIT QUALITY ELENDİ:", (decision or {}).get("symbol"), reason)
            return None
        enriched = _reanchor_signal(signal, decision)
        _RUN_COUNTS["ACCEPTED"] += 1
        _RUN_ACCEPTED.append({
            "symbol": enriched.get("symbol"),
            "direction": enriched.get("direction"),
            "score": enriched.get("score"),
            "risk_percent": enriched.get("risk_percent"),
            "tp1_percent": round(_directional_percent(str(enriched.get("direction")), _sf(enriched.get("entry")), _sf(enriched.get("tp1"))), 3),
            "tp2_percent": round(_directional_percent(str(enriched.get("direction")), _sf(enriched.get("entry")), _sf(enriched.get("tp2"))), 3),
            "tp3_percent": round(_directional_percent(str(enriched.get("direction")), _sf(enriched.get("entry")), _sf(enriched.get("tp3"))), 3),
            "target_r": enriched.get("rr_tp3"),
            "target_source": enriched.get("profit_target_source"),
        })
        return enriched

    def send_single_entry_mode(text: str, delivery_key=None):
        message = str(text or "").strip()
        # Big Move keeps its internal ledger but no longer creates a second entry message.
        if message.startswith("🚀 BÜYÜK HAREKET BAŞLANGICI"):
            print("PROFIT QUALITY | Big Move Telegram sessiz, iç takip aktif")
            return True
        return original_send(text, delivery_key=delivery_key)

    # PREP/EARLY analysis continues internally; only their Telegram visibility is disabled.
    simple_mode.early_entry_eligible = lambda plan: False
    simple_mode.early_move_eligible = lambda decision: False

    runner.analyze_candidate = analyze_with_profit_target
    runner.decision_to_signal = decision_to_signal_profit_quality
    runner._format_trade_message = _trade_message
    runner._send = send_single_entry_mode


def finish() -> Dict[str, Any]:
    return _save_summary()


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "telegram_entry_messages": "FINAL_TRADE_ONLY",
        "early_entry_telegram": False,
        "big_move_telegram": False,
        "big_move_internal_tracking": True,
        "min_score": MIN_SCORE,
        "min_expected_move_percent": MIN_EXPECTED_MOVE_PERCENT,
        "min_target_r": MIN_TARGET_R,
        "max_risk_percent": MAX_RISK_PERCENT,
        "min_volume_ratio_5m": MIN_VOLUME_RATIO_5M,
        "tp1_min_percent_at_threshold": round(MIN_EXPECTED_MOVE_PERCENT * TP1_FRACTION, 2),
        "tp2_min_percent_at_threshold": round(MIN_EXPECTED_MOVE_PERCENT * TP2_FRACTION, 2),
        "tp3_min_percent_at_threshold": round(MIN_EXPECTED_MOVE_PERCENT * TP3_FRACTION, 2),
    }
