"""Selective Telegram visibility for strong live candidates rejected by final gates.

This layer does not create trades, bypass any gate, place orders, or widen stops.
It is installed outside the complete Market First decision pipeline. If the
pipeline rejects a candidate that had already become trade-eligible, this module
may emit a clearly labelled observational Telegram message for only the strongest
aligned candidates so high-quality opportunities are not completely invisible.

Final trade Telegram remains authoritative. Candidate messages are explicitly
marked as non-final and keep the rejection reason visible.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Tuple

import market_first_final_execution_gate as final_gate
import market_first_profit_quality_v1 as profit_quality
import market_first_profit_survival_gate as survival_gate
import market_first_runner as runner

VERSION = "MARKET_FIRST_CANDIDATE_VISIBILITY_V1_2026_09_13"
STATE_FILE = "market_first_candidate_visibility.json"
MODE = "OBSERVATIONAL_STRONG_REJECTED_CANDIDATES_NO_GATE_BYPASS"

MIN_SCORE = 92
MAX_RISK_PERCENT = 0.90
MIN_EXPECTED_MOVE_PERCENT = 1.25
MIN_TARGET_R = 1.80
MIN_VOLUME_RATIO = 0.80
MIN_CONFIRMATIONS = 3
MAX_EXTENSION_ATR = 1.25
MAX_ALERTS_PER_RUN = 2
ALERT_COOLDOWN_SECONDS = 45 * 60

_INSTALLED = False
_RUN_ALERTS = 0


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


def candidate_is_visible(decision: Mapping[str, Any] | None) -> Tuple[bool, str]:
    if not isinstance(decision, Mapping):
        return False, "NO_DECISION"
    if not bool(decision.get("trade_eligible")):
        return False, "NOT_TRADE_ELIGIBLE"

    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, "DIRECTION"
    if str(decision.get("stage") or "").upper() == "LATE":
        return False, "LATE"

    score = _si(decision.get("score"))
    if score < MIN_SCORE:
        return False, "SCORE"

    risk = _sf(decision.get("risk_percent"), 99.0)
    if risk <= 0 or risk > MAX_RISK_PERCENT:
        return False, "RISK"

    extension = _sf(decision.get("extension_atr_5m"), 999.0)
    if extension > MAX_EXTENSION_ATR:
        return False, "EXTENSION"

    s5 = str(decision.get("structure_5m") or "").upper()
    s15 = str(decision.get("structure_15m") or "").upper()
    s1h = str(decision.get("structure_1h") or "").upper()
    if not all(item == direction for item in (s5, s15, s1h)):
        return False, "STRUCTURE"

    preferred = str(decision.get("market_preferred_direction") or "").upper()
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if preferred == opposite:
        return False, "MARKET_OPPOSITE"

    engine = _engine(decision)
    if bool(engine.get("reversal")):
        return False, "REVERSAL"
    confirmations = _si(engine.get("confirmations"))
    if engine and confirmations < MIN_CONFIRMATIONS:
        return False, "CONFIRMATIONS"

    volume = max(
        _sf(decision.get("volume_ratio_5m")),
        _sf(decision.get("volume_ratio_1m")),
        _sf(decision.get("volume_ratio")),
    )
    if volume < MIN_VOLUME_RATIO:
        return False, "VOLUME"

    expected = max(
        _sf(decision.get("expected_move_percent")),
        _sf(decision.get("profit_target_percent")),
    )
    if expected < MIN_EXPECTED_MOVE_PERCENT:
        return False, "EXPECTED_MOVE"

    target_r = max(
        _sf(decision.get("technical_target_r")),
        _sf(decision.get("profit_target_r")),
        _sf(decision.get("room_r")),
    )
    if target_r < MIN_TARGET_R:
        return False, "TARGET_R"
    return True, "OK"


def rejection_reason(decision: Mapping[str, Any]) -> str:
    try:
        ok, reason = profit_quality._quality_reason(decision)
        if not ok:
            return f"PROFIT_QUALITY:{reason}"
    except Exception:
        pass

    try:
        ok, reason, _ = final_gate._execution_reason(decision)
        if not ok:
            return f"FINAL_EXECUTION:{reason}"
    except Exception:
        pass

    try:
        health = survival_gate._current_health()
        mode = str(health.get("mode") or "NORMAL")
        if mode == "HALT":
            return f"SURVIVAL:{health.get('reason') or 'HALT'}"
        if mode == "RECOVERY_STRICT":
            ok, reason, _ = survival_gate._recovery_reason(decision)
            if not ok:
                return f"SURVIVAL:{reason}"
    except Exception:
        pass
    return "FINAL_PIPELINE_REJECTED"


def _load_state() -> Dict[str, Any]:
    data = runner.bot.load_json_file(STATE_FILE, {"version": VERSION, "last_sent": {}})
    if not isinstance(data, dict):
        data = {"version": VERSION, "last_sent": {}}
    data.setdefault("version", VERSION)
    data.setdefault("last_sent", {})
    if not isinstance(data.get("last_sent"), dict):
        data["last_sent"] = {}
    return data


def _alert_key(decision: Mapping[str, Any]) -> str:
    return f"{decision.get('symbol')}:{str(decision.get('direction') or '').upper()}"


def _can_send(state: Mapping[str, Any], decision: Mapping[str, Any], now: int) -> bool:
    last_sent = state.get("last_sent") if isinstance(state, Mapping) else {}
    if not isinstance(last_sent, Mapping):
        return True
    last = _si(last_sent.get(_alert_key(decision)))
    return now - last >= ALERT_COOLDOWN_SECONDS


def _message(decision: Mapping[str, Any], reason: str) -> str:
    direction = str(decision.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    expected = max(
        _sf(decision.get("expected_move_percent")),
        _sf(decision.get("profit_target_percent")),
    )
    target_r = max(
        _sf(decision.get("technical_target_r")),
        _sf(decision.get("profit_target_r")),
        _sf(decision.get("room_r")),
    )
    volume = max(
        _sf(decision.get("volume_ratio_5m")),
        _sf(decision.get("volume_ratio_1m")),
        _sf(decision.get("volume_ratio")),
    )
    return (
        f"🟠 GÜÇLÜ CANLI ADAY | {decision.get('symbol')}\n"
        f"{icon} {direction} | skor {int(_sf(decision.get('score')))}/100\n"
        f"💵 Fiyat: {runner.bot.format_price(decision.get('current_price'))}\n"
        f"🛑 Risk: %{_sf(decision.get('risk_percent')):.2f}\n"
        f"🎯 Beklenen alan: ~%{expected:.2f} | {target_r:.2f}R\n"
        f"🔊 Hacim teyidi: {volume:.2f}x\n"
        f"🚧 Nihai kapı: {reason}\n"
        "⚠️ NİHAİ İŞLEM SİNYALİ DEĞİLDİR. Final onay bekleyen güçlü adaydır; otomatik emir açılmaz."
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original = runner.decision_to_signal
    send = runner._send
    state = _load_state()

    def decision_to_signal_with_visibility(decision):
        global _RUN_ALERTS
        signal = original(decision)
        if isinstance(signal, Mapping):
            return signal
        if _RUN_ALERTS >= MAX_ALERTS_PER_RUN:
            return signal

        visible, _ = candidate_is_visible(decision if isinstance(decision, Mapping) else None)
        if not visible:
            return signal

        now = runner.bot.now_ts()
        if not _can_send(state, decision, now):
            return signal

        reason = rejection_reason(decision)
        sent = send(
            _message(decision, reason),
            delivery_key=f"CANDIDATE_VISIBILITY:{_alert_key(decision)}:{now // ALERT_COOLDOWN_SECONDS}",
        )
        if sent:
            _RUN_ALERTS += 1
            state["version"] = VERSION
            state.setdefault("last_sent", {})[_alert_key(decision)] = now
            state["last_alert"] = {
                "at": now,
                "symbol": decision.get("symbol"),
                "direction": decision.get("direction"),
                "score": decision.get("score"),
                "reason": reason,
            }
            if runner._is_live_run():
                runner.bot.save_json_file(STATE_FILE, state)
            print("GÜÇLÜ CANLI ADAY TELEGRAM:", decision.get("symbol"), decision.get("direction"), reason)
        return signal

    runner.decision_to_signal = decision_to_signal_with_visibility


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "min_score": MIN_SCORE,
        "max_risk_percent": MAX_RISK_PERCENT,
        "min_expected_move_percent": MIN_EXPECTED_MOVE_PERCENT,
        "min_target_r": MIN_TARGET_R,
        "min_volume_ratio": MIN_VOLUME_RATIO,
        "min_confirmations": MIN_CONFIRMATIONS,
        "max_extension_atr": MAX_EXTENSION_ATR,
        "max_alerts_per_run": MAX_ALERTS_PER_RUN,
        "cooldown_minutes": ALERT_COOLDOWN_SECONDS // 60,
        "alerts_this_run": _RUN_ALERTS,
        "bypasses_final_trade_gate": False,
    }
