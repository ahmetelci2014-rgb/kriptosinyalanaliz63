"""Selective Telegram pre-signal lane for Market First V6.

Purpose:
- surface only strong background ENTRY plans that narrowly miss one soft final gate,
- never convert them into real trades,
- never save them to open_signals/trade_ledger,
- never bypass Profit Quality, Final Execution, portfolio, cooldown or send guards.

A real trade still has to pass the ordinary pipeline and is sent separately.
"""
from __future__ import annotations

from collections import Counter
import math
from typing import Any, Dict, Mapping, Tuple

import market_first_runner as runner
import market_first_profit_quality_v1 as profit_quality
import market_first_final_execution_gate as final_execution

VERSION = "MARKET_FIRST_SELECTIVE_PRE_SIGNAL_V2_2026_09_22"
MODE = "STRONG_ENTRY_NEAR_MISS_TELEGRAM_ONLY_NO_TRADE"

MIN_SCORE = 92
MAX_RISK_PERCENT = 0.90
MIN_TECHNICAL_EXPECTED_MOVE_PERCENT = 1.25
MIN_TECHNICAL_TARGET_R = 2.00
MIN_DIRECTION_SCORE = 85
MIN_DIRECTION_MARGIN = 40
MIN_DIRECTION_CONFIRMATIONS = 3
MIN_VOLUME_RATIO = 0.65
MIN_SOFT_VOLUME_RATIO = 0.55
MAX_PER_RUN = 1

_SOFT_QUALITY_REASONS = {
    "EXPECTED_MOVE_BELOW_2P",
    "TARGET_R_BELOW_2_5",
    "VOLUME_BELOW_0_65",
}
_SOFT_EXECUTION_REASONS = {
    "NO_FRESH_MICRO_TAKER_WEAK",
    "NO_FRESH_MICRO_CVD_WEAK",
    "NO_FRESH_MICRO_DERIVATIVES_WEAK",
}

_REASON_LABELS = {
    "EXPECTED_MOVE_BELOW_2P": "Kâr alanı final işlem eşiğinin biraz altında",
    "TARGET_R_BELOW_2_5": "Risk/ödül final işlem eşiğinin biraz altında",
    "VOLUME_BELOW_0_65": "Hacim final işlem eşiğinin biraz altında",
    "NO_FRESH_MICRO_TAKER_WEAK": "5M taze tetik / alıcı-satıcı akışı teyidi bekleniyor",
    "NO_FRESH_MICRO_CVD_WEAK": "5M taze tetik / CVD teyidi bekleniyor",
    "NO_FRESH_MICRO_DERIVATIVES_WEAK": "5M taze tetik / türev teyidi bekleniyor",
}

_INSTALLED = False
_RUN_COUNTS: Counter = Counter()
_RUN_SENT: list[Dict[str, Any]] = []


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _si(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _engine(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    value = decision.get("direction_engine")
    return value if isinstance(value, Mapping) else {}


def _flags(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _engine(decision).get("confirmation_flags")
    return value if isinstance(value, Mapping) else {}


def _volume_ratio(decision: Mapping[str, Any]) -> float:
    return max(
        _sf(decision.get("volume_ratio_5m")),
        _sf(decision.get("volume_ratio_1m")),
        _sf(decision.get("volume_ratio")),
    )


def _technical_target_r(decision: Mapping[str, Any]) -> float:
    explicit = _sf(decision.get("technical_target_r"))
    if explicit > 0:
        return explicit
    expected = _sf(decision.get("expected_move_percent"))
    risk = _sf(decision.get("risk_percent"))
    return expected / risk if expected > 0 and risk > 0 else 0.0


def _base_strength(decision: Mapping[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, "DIRECTION", {}

    if not bool(decision.get("entry_plan_trade")):
        return False, "NOT_ENTRY_PLAN", {}

    if str(decision.get("stage") or "").upper() != "READY":
        return False, "NOT_READY", {}

    if not bool(decision.get("trade_eligible")):
        return False, "NOT_TRADE_ELIGIBLE", {}

    score = _si(decision.get("score"))
    risk = _sf(decision.get("risk_percent"), 999.0)
    expected = _sf(decision.get("expected_move_percent"))
    target_r = _technical_target_r(decision)
    volume = _volume_ratio(decision)

    if score < MIN_SCORE:
        return False, "SCORE", {"score": score}
    if risk <= 0 or risk > MAX_RISK_PERCENT:
        return False, "RISK", {"risk_percent": risk}
    if expected < MIN_TECHNICAL_EXPECTED_MOVE_PERCENT:
        return False, "EXPECTED_MOVE", {"expected_move_percent": expected}
    if target_r < MIN_TECHNICAL_TARGET_R:
        return False, "TARGET_R", {"technical_target_r": target_r}

    preferred = str(decision.get("market_preferred_direction") or "").upper()
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if preferred == opposite:
        return False, "MARKET_OPPOSITE", {"preferred": preferred}

    if str(decision.get("structure_15m") or "").upper() != direction:
        return False, "STRUCTURE_15M", {}
    if str(decision.get("structure_1h") or "").upper() != direction:
        return False, "STRUCTURE_1H", {}

    engine = _engine(decision)
    selected = str(engine.get("selected_direction") or "").upper()
    selected_score = _si(engine.get("selected_score"))
    margin = _si(engine.get("margin"))
    confirmations = _si(engine.get("confirmations"))

    if selected != direction:
        return False, "DIRECTION_ENGINE", {"selected": selected}
    if selected_score < MIN_DIRECTION_SCORE or margin < MIN_DIRECTION_MARGIN:
        return False, "DIRECTION_STRENGTH", {
            "selected_score": selected_score,
            "margin": margin,
        }
    if confirmations < MIN_DIRECTION_CONFIRMATIONS:
        return False, "CONFIRMATIONS", {"confirmations": confirmations}

    evidence = {
        "score": score,
        "risk_percent": round(risk, 4),
        "expected_move_percent": round(expected, 4),
        "technical_target_r": round(target_r, 3),
        "volume_ratio": round(volume, 3),
        "direction_score": selected_score,
        "direction_margin": margin,
        "confirmations": confirmations,
        "fresh_micro": bool(_flags(decision).get("fresh_micro")),
    }
    return True, "OK", evidence


def evaluate_near_signal(decision: Mapping[str, Any] | None) -> Tuple[bool, str, Dict[str, Any]]:
    if not isinstance(decision, Mapping):
        return False, "INVALID", {}

    ok, reason, evidence = _base_strength(decision)
    if not ok:
        return False, reason, evidence

    quality_ok, quality_reason = profit_quality._quality_reason(decision)
    execution_ok, execution_reason, execution_evidence = final_execution._execution_reason(decision)

    volume = _volume_ratio(decision)
    fresh_micro = bool(_flags(decision).get("fresh_micro"))

    # Visibility lane: once the candidate itself is strong enough to pass
    # _base_strength, a failed final trade gate should remain visible to the user
    # as a BACKGROUND candidate instead of disappearing completely. This does not
    # promote it to a trade and does not write open_signals/trade_ledger.
    if not quality_ok:
        out = dict(evidence)
        out["near_reason"] = quality_reason
        out["execution"] = execution_evidence
        out["visibility_only"] = True

        if quality_reason == "VOLUME_BELOW_0_65":
            if volume < MIN_SOFT_VOLUME_RATIO:
                return False, "BACKGROUND_VOLUME_TOO_WEAK", evidence

        if not execution_ok:
            reason = f"BACKGROUND_MULTI_BLOCK:{quality_reason}+{execution_reason}"
            return True, reason, out

        if quality_reason in _SOFT_QUALITY_REASONS:
            return True, quality_reason, out

        return True, f"BACKGROUND_QUALITY:{quality_reason}", out

    if not execution_ok:
        out = dict(evidence)
        out["near_reason"] = execution_reason
        out["execution"] = execution_evidence
        out["visibility_only"] = True

        if execution_reason in _SOFT_EXECUTION_REASONS:
            if volume < MIN_VOLUME_RATIO:
                return False, "EXECUTION_NEAR_BUT_VOLUME_WEAK", evidence
            return True, execution_reason, out

        return True, f"BACKGROUND_EXECUTION:{execution_reason}", out

    return False, "WOULD_PASS_REAL_PIPELINE", evidence


def _fingerprint(decision: Mapping[str, Any]) -> str:
    symbol = str(decision.get("symbol") or "UNKNOWN")
    direction = str(decision.get("direction") or "NA").upper()
    ideal = _sf(decision.get("entry_plan_ideal_entry")) or _sf(decision.get("current_price"))
    zone_low = _sf(decision.get("entry_plan_zone_low"))
    zone_high = _sf(decision.get("entry_plan_zone_high"))
    return f"NEAR:{symbol}:{direction}:{ideal:.8g}:{zone_low:.8g}:{zone_high:.8g}"


def _format_message(decision: Mapping[str, Any], reason: str, evidence: Mapping[str, Any]) -> str:
    direction = str(decision.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    zone_low = _sf(decision.get("entry_plan_zone_low"))
    zone_high = _sf(decision.get("entry_plan_zone_high"))
    ideal = _sf(decision.get("entry_plan_ideal_entry")) or _sf(decision.get("current_price"))
    expected = _sf(evidence.get("expected_move_percent"))
    target_r = _sf(evidence.get("technical_target_r"))
    risk = _sf(evidence.get("risk_percent"))
    score = _si(evidence.get("score"))
    if reason.startswith("BACKGROUND_QUALITY:"):
        reason_text = "Kalite kapısındaki son şart eksik: " + reason.split(":", 1)[1]
    elif reason.startswith("BACKGROUND_EXECUTION:"):
        reason_text = "Son giriş teyidi eksik: " + reason.split(":", 1)[1]
    elif reason.startswith("BACKGROUND_MULTI_BLOCK:"):
        reason_text = "Birden fazla son teyit eksik: " + reason.split(":", 1)[1]
    else:
        reason_text = _REASON_LABELS.get(reason, reason)

    if zone_low > 0 and zone_high > 0:
        zone_line = (
            f"📍 İzleme bölgesi: {runner.bot.format_price(zone_low)} - "
            f"{runner.bot.format_price(zone_high)}\n"
        )
    else:
        zone_line = f"📍 İzleme fiyatı: {runner.bot.format_price(ideal)}\n"

    return (
        f"👀 ARKA PLAN ADAYI | {decision.get('symbol')}\n"
        f"{icon} {direction} | Piyasa: {decision.get('market_label') or '-'}\n"
        f"{zone_line}"
        f"⭐ Skor: {score} | Risk: %{risk:.2f}\n"
        f"🎯 Teknik potansiyel: %{expected:.2f} | ~{target_r:.2f}R\n"
        f"⏳ İşleme dönüşmeme nedeni: {reason_text}\n"
        f"⚠️ İZLEME AMAÇLIDIR — GERÇEK İŞLEM DEĞİL. Son teyit gelirse ayrıca ✅ İŞLEM FIRSATI mesajı gelir."
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_decision_to_signal = runner.decision_to_signal

    def decision_to_signal_with_near_alert(decision):
        signal = original_decision_to_signal(decision)
        if isinstance(signal, Mapping):
            return signal

        if len(_RUN_SENT) >= MAX_PER_RUN:
            _RUN_COUNTS["RUN_LIMIT"] += 1
            return signal

        ok, reason, evidence = evaluate_near_signal(decision)
        _RUN_COUNTS[reason] += 1
        if not ok:
            return signal

        symbol = str((decision or {}).get("symbol") or "")
        try:
            if runner.bot.has_open_same_symbol(symbol):
                _RUN_COUNTS["OPEN_SYMBOL"] += 1
                return signal
            if runner.bot.has_recent_stop(symbol):
                _RUN_COUNTS["RECENT_STOP"] += 1
                return signal
        except Exception:
            _RUN_COUNTS["COOLDOWN_CHECK_ERROR"] += 1
            return signal

        message = _format_message(decision, reason, evidence)
        sent = runner._send(message, delivery_key=_fingerprint(decision))
        if sent:
            _RUN_SENT.append({
                "symbol": symbol,
                "direction": str((decision or {}).get("direction") or ""),
                "reason": reason,
                **dict(evidence),
            })
            _RUN_COUNTS["SENT"] += 1
            print("SELECTIVE PRE-SIGNAL GÖNDERİLDİ:", symbol, reason)
        else:
            _RUN_COUNTS["SEND_FAILED"] += 1

        return signal

    runner.decision_to_signal = decision_to_signal_with_near_alert


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": "STRONG_BACKGROUND_VISIBILITY_NO_TRADE",
        "max_per_run": MAX_PER_RUN,
        "min_score": MIN_SCORE,
        "max_risk_percent": MAX_RISK_PERCENT,
        "min_expected_move_percent": MIN_TECHNICAL_EXPECTED_MOVE_PERCENT,
        "min_technical_target_r": MIN_TECHNICAL_TARGET_R,
        "real_trade_promotion": False,
        "open_signal_write": False,
        "trade_ledger_write": False,
        "exchange_orders": False,
        "run_counts": dict(_RUN_COUNTS),
        "sent": list(_RUN_SENT),
    }
