"""Premium Early Breakout live route.

Promotes only selected Movement Start V2 5M structures into the existing Premium
live pipeline. It opens no exchange orders. Any promoted signal still passes the
main Market Guard, entry validity, duplicate, open-risk, portfolio-risk, cost and
Telegram/ledger layers.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Callable, Dict, Optional, Tuple

import movement_start_v3_orderflow_shadow as orderflow
import strategy

VERSION = "PREMIUM_EARLY_BREAKOUT_V1_2026_08_24"
SOURCE = "EARLY_BREAKOUT_ENTRY"
STATE_FILE = "premium_early_breakout_state.json"

MIN_BASE_SCORE = 74
MIN_LIVE_SCORE = 91
MIN_DIRECTION_GAP = 10
MIN_RISK_PERCENT = 0.35
MAX_RISK_PERCENT = 1.60
MAX_EXTRA_FLOW_QUERIES_PER_RUN = 4
KEEP_SECONDS = 14 * 24 * 60 * 60
MAX_RECORDS = 1200

STAGE_MAX_DRIFT = {
    "PREP": 0.30,
    "ARMED": 0.35,
    "TRIGGER": 0.45,
}

_STATE: Optional[Dict[str, Any]] = None
_STATE_PATH = STATE_FILE
_DIRTY = False
_EXTRA_FLOW_QUERIES = 0


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _default_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": 0,
        "records": [],
        "summary": {},
    }


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".early_breakout.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def begin(path: str = STATE_FILE) -> None:
    global _STATE, _STATE_PATH, _DIRTY, _EXTRA_FLOW_QUERIES
    _STATE_PATH = path
    _DIRTY = False
    _EXTRA_FLOW_QUERIES = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = _default_state()
    except Exception:
        data = _default_state()
    data.setdefault("records", [])
    data.setdefault("summary", {})
    data["version"] = VERSION
    _STATE = data


def _state() -> Dict[str, Any]:
    global _STATE
    if _STATE is None:
        begin()
    return _STATE if isinstance(_STATE, dict) else _default_state()


def _condition(conditions: Dict[str, Any], name: str) -> bool:
    return bool((conditions or {}).get(name))


def _flow_from_raw(symbol: str, direction: str, now: int) -> Optional[Dict[str, Any]]:
    global _EXTRA_FLOW_QUERIES
    if _EXTRA_FLOW_QUERIES >= MAX_EXTRA_FLOW_QUERIES_PER_RUN:
        return None
    _EXTRA_FLOW_QUERIES += 1
    try:
        flow = orderflow.fetch_order_flow(symbol, now)
    except Exception:
        flow = None
    if not isinstance(flow, dict):
        return None
    try:
        score, conditions, pressure_delta = orderflow.score_order_flow(flow, direction)
    except Exception:
        return None
    confirmed = bool(
        score >= int(getattr(orderflow, "CONFIRM_SCORE", 65))
        and conditions.get("spread_ok")
        and (
            (conditions.get("book_support") and conditions.get("recent_trade_support"))
            or conditions.get("strong_flow")
        )
    )
    return {
        "symbol": symbol,
        "direction": direction,
        "at": now,
        "orderflow_score": int(score),
        "orderflow_confirmed": confirmed,
        "pressure_delta": pressure_delta,
        "flow": flow,
        "conditions": conditions,
        "targeted_live_query": True,
    }


def _normalize_flow(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {
            "available": False,
            "score": None,
            "confirmed": False,
            "spread_bps": None,
            "conditions": {},
        }
    flow = snapshot.get("flow") if isinstance(snapshot.get("flow"), dict) else {}
    return {
        "available": True,
        "score": int(_sf(snapshot.get("orderflow_score"), 0) or 0),
        "confirmed": bool(snapshot.get("orderflow_confirmed")),
        "spread_bps": _sf(flow.get("spread_bps")),
        "book_imbalance": _sf(flow.get("book_imbalance")),
        "trade_imbalance": _sf(flow.get("trade_imbalance")),
        "recent_trade_imbalance": _sf(flow.get("recent_trade_imbalance")),
        "conditions": snapshot.get("conditions") if isinstance(snapshot.get("conditions"), dict) else {},
        "targeted_live_query": bool(snapshot.get("targeted_live_query")),
    }


def _exceptional_prep(
    base_score: int,
    direction_gap: int,
    risk_percent: float,
    features: Dict[str, Any],
    conditions: Dict[str, Any],
    four_hour_ok: bool,
) -> bool:
    volume_ratio = _sf(features.get("volume_ratio"), 0.0) or 0.0
    volume_wake = _sf(features.get("volume_wake"), 0.0) or 0.0
    common = bool(
        base_score >= 74
        and direction_gap >= 15
        and _condition(conditions, "squeeze")
        and _condition(conditions, "structure_hold")
        and volume_ratio >= 1.40
        and volume_wake >= 1.20
        and _condition(conditions, "close_power")
        and _condition(conditions, "ema_turn")
        and _condition(conditions, "rsi_turn")
        and _condition(conditions, "fifteen_not_opposing")
        and _condition(conditions, "one_hour_not_opposing")
        and MIN_RISK_PERCENT <= risk_percent <= 1.10
    )
    if not common:
        return False
    if four_hour_ok:
        return True
    # NES tipi: 4H eski yönü hâlâ taşırken 5M/15M/1H kırılımı çok belirgin olmalı.
    return bool(
        direction_gap >= 20
        and risk_percent <= 1.00
        and volume_ratio >= 1.50
        and volume_wake >= 1.30
    )


def _reject_reason(
    base_result: Dict[str, Any],
    current_price: float,
    flow_info: Dict[str, Any],
) -> Tuple[Optional[str], Dict[str, Any]]:
    direction = str(base_result.get("direction") or "").upper()
    stage = str(base_result.get("stage") or "").upper()
    base_score = int(_sf(base_result.get("score"), 0) or 0)
    opposite_score = int(_sf(base_result.get("opposite_score"), 0) or 0)
    gap = base_score - opposite_score
    features = base_result.get("features") if isinstance(base_result.get("features"), dict) else {}
    conditions = base_result.get("conditions") if isinstance(base_result.get("conditions"), dict) else {}
    anchor = _sf(base_result.get("entry"), 0.0) or 0.0
    stop = _sf(base_result.get("stop"), 0.0) or 0.0

    context = {
        "direction": direction,
        "stage": stage,
        "base_score": base_score,
        "opposite_score": opposite_score,
        "direction_gap": gap,
        "anchor": anchor,
        "stop": stop,
        "features": features,
        "conditions": conditions,
    }

    if direction not in {"LONG", "SHORT"}:
        return "YON_YOK", context
    if stage not in {"PREP", "ARMED", "TRIGGER"}:
        return "ASAMA_YETERSIZ", context
    if base_score < MIN_BASE_SCORE:
        return "BASE_SKOR_DUSUK", context
    if gap < MIN_DIRECTION_GAP:
        return "YON_FARKI_ZAYIF", context
    if anchor <= 0 or stop <= 0 or current_price <= 0:
        return "FIYAT_STOP_YETERSIZ", context

    drift = abs(current_price - anchor) / anchor * 100.0
    context["anchor_drift_percent"] = drift
    if drift > STAGE_MAX_DRIFT[stage]:
        return "HAREKET_KACMIS", context

    if direction == "LONG" and stop >= current_price:
        return "STOP_YANLIS_TARAFTA", context
    if direction == "SHORT" and stop <= current_price:
        return "STOP_YANLIS_TARAFTA", context

    targets = strategy.make_targets_from_stop(direction, current_price, stop)
    if not isinstance(targets, dict):
        return "RISK_HEDEF_UYGUN_DEGIL", context
    risk_percent = float(targets.get("risk_percent") or 0.0)
    context["targets"] = targets
    context["risk_percent"] = risk_percent
    if not (MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT):
        return "EARLY_RISK_DISI", context

    fifteen_ok = _condition(conditions, "fifteen_not_opposing")
    one_hour_ok = _condition(conditions, "one_hour_not_opposing")
    four_hour_ok = _condition(conditions, "four_hour_not_opposing")
    context["four_hour_ok"] = four_hour_ok
    if not fifteen_ok:
        return "15M_TERS", context
    if not one_hour_ok:
        return "1H_TERS", context

    volume_ratio = _sf(features.get("volume_ratio"), 0.0) or 0.0
    core_count = sum(
        int(value)
        for value in (
            _condition(conditions, "squeeze"),
            _condition(conditions, "structure_hold"),
            _condition(conditions, "internal_break"),
            _condition(conditions, "volume_wake"),
            _condition(conditions, "volume_confirm"),
            _condition(conditions, "ema_turn"),
            _condition(conditions, "rsi_turn"),
            _condition(conditions, "close_power"),
        )
    )
    context["core_count"] = core_count
    exceptional = _exceptional_prep(
        base_score,
        gap,
        risk_percent,
        features,
        conditions,
        four_hour_ok,
    )
    context["exceptional"] = exceptional

    if stage == "PREP":
        if not exceptional:
            return "PREP_HENUZ_YETERSIZ", context
    elif stage == "ARMED":
        if not _condition(conditions, "volume_confirm") or core_count < 5:
            return "ARMED_KIRILIM_ZAYIF", context
        if base_score < 82 and not exceptional and not flow_info.get("confirmed"):
            return "ARMED_EK_TEYIT_GEREKLI", context
    else:
        if not _condition(conditions, "internal_break") or not _condition(conditions, "volume_confirm"):
            return "TRIGGER_YAPI_EKSIK", context
        if not four_hour_ok and not (exceptional or flow_info.get("confirmed") or base_score >= 94):
            return "4H_TERS_TRIGGER_TEYIDI_YETERSIZ", context

    if flow_info.get("available"):
        spread = _sf(flow_info.get("spread_bps"))
        flow_score = int(_sf(flow_info.get("score"), 0) or 0)
        if spread is not None and spread > 25.0:
            return "SPREAD_COK_GENIS", context
        if flow_score < 20 and not flow_info.get("confirmed"):
            return "ORDERFLOW_TERS_ZAYIF", context
        if stage in {"PREP", "ARMED"} and flow_score < 35 and not flow_info.get("confirmed"):
            return "ORDERFLOW_EK_TEYIT_YETERSIZ", context
        if stage == "TRIGGER" and flow_score < 25 and not flow_info.get("confirmed"):
            return "ORDERFLOW_TRIGGER_ZAYIF", context

    return None, context


def _live_score(context: Dict[str, Any], flow_info: Dict[str, Any]) -> int:
    stage = context["stage"]
    base_score = int(context["base_score"])
    features = context["features"]
    conditions = context["conditions"]
    gap = int(context["direction_gap"])

    score = 88
    score += {"PREP": 0, "ARMED": 2, "TRIGGER": 4}.get(stage, 0)
    score += min(3, max(0, (base_score - 74) // 5))
    score += int(_condition(conditions, "squeeze"))
    score += int((_sf(features.get("volume_ratio"), 0.0) or 0.0) >= 1.50)
    score += int((_sf(features.get("volume_wake"), 0.0) or 0.0) >= 1.30)
    score += int(_condition(conditions, "internal_break"))
    score += int(bool(flow_info.get("confirmed"))) * 2
    score += int(bool(context.get("four_hour_ok")))
    score += int(gap >= 20)
    return max(0, min(100, int(score)))


def _record(
    symbol: str,
    base_result: Dict[str, Any],
    decision: str,
    reason: str,
    context: Dict[str, Any],
    flow_info: Dict[str, Any],
    live_score: Optional[int],
    now: int,
) -> None:
    global _DIRTY
    state = _state()
    records = state.setdefault("records", [])
    records.append({
        "at": now,
        "symbol": str(symbol or "").upper(),
        "direction": base_result.get("direction"),
        "stage": base_result.get("stage"),
        "base_score": base_result.get("score"),
        "opposite_score": base_result.get("opposite_score"),
        "live_score": live_score,
        "decision": decision,
        "reason": reason,
        "anchor_drift_percent": round(float(context.get("anchor_drift_percent") or 0.0), 4),
        "risk_percent": round(float(context.get("risk_percent") or 0.0), 4),
        "core_count": context.get("core_count"),
        "exceptional": bool(context.get("exceptional")),
        "four_hour_ok": bool(context.get("four_hour_ok")),
        "flow_available": bool(flow_info.get("available")),
        "flow_score": flow_info.get("score"),
        "flow_confirmed": bool(flow_info.get("confirmed")),
        "targeted_flow_query": bool(flow_info.get("targeted_live_query")),
    })
    cutoff = now - KEEP_SECONDS
    records[:] = [r for r in records if int(r.get("at") or 0) >= cutoff][-MAX_RECORDS:]
    _DIRTY = True


def analyze_live_candidate(
    symbol: str,
    base_result: Optional[Dict[str, Any]],
    current_price: Any,
    flow_snapshot: Optional[Dict[str, Any]] = None,
    *,
    now_ts: Optional[int] = None,
    allow_extra_flow: bool = True,
) -> Optional[Dict[str, Any]]:
    if not isinstance(base_result, dict):
        return None
    base_score = int(_sf(base_result.get("score"), 0) or 0)
    if base_score < MIN_BASE_SCORE:
        return None

    now = int(now_ts if now_ts is not None else time.time())
    symbol = str(symbol or base_result.get("symbol") or "").upper()
    direction = str(base_result.get("direction") or "").upper()
    price = _sf(current_price, _sf(base_result.get("entry"), 0.0)) or 0.0

    flow_info = _normalize_flow(flow_snapshot)
    if not flow_info.get("available") and allow_extra_flow and direction in {"LONG", "SHORT"}:
        stage = str(base_result.get("stage") or "").upper()
        if stage in {"ARMED", "TRIGGER"} or base_score >= 74:
            targeted = _flow_from_raw(symbol, direction, now)
            flow_info = _normalize_flow(targeted)

    reject, context = _reject_reason(base_result, price, flow_info)
    if reject:
        _record(symbol, base_result, "REJECTED", reject, context, flow_info, None, now)
        return None

    live_score = _live_score(context, flow_info)
    if live_score < MIN_LIVE_SCORE:
        _record(symbol, base_result, "REJECTED", "LIVE_SKOR_DUSUK", context, flow_info, live_score, now)
        return None

    targets = context["targets"]
    risk_percent = float(context["risk_percent"])
    features = context["features"]
    stage = context["stage"]
    base_score = int(context["base_score"])
    flow_score = flow_info.get("score")
    flow_text = (
        f"CONFIRMED {flow_score}/100"
        if flow_info.get("confirmed")
        else f"{flow_score}/100"
        if flow_info.get("available")
        else "veri yok / yapı teyidi güçlü"
    )
    quality = "A+ ERKEN BREAKOUT" if live_score >= 97 else "A ERKEN BREAKOUT"
    leverage = "2x" if risk_percent <= 0.85 else "1x-2x"
    drift = float(context.get("anchor_drift_percent") or 0.0)

    candidate = {
        "symbol": symbol,
        "direction": direction,
        "source": SOURCE,
        "signal_class": "TRADE",
        "entry": round(price, 12),
        "ideal_entry": round(float(context["anchor"]), 12),
        "zone_name": f"5M {stage} early breakout",
        "zone_distance_percent": round(drift, 4),
        "tp1": round(float(targets["tp1"]), 12),
        "tp2": round(float(targets["tp2"]), 12),
        "tp3": round(float(targets["tp3"]), 12),
        "sl": round(float(targets["sl"]), 12),
        "risk_percent": round(risk_percent, 4),
        "rr_tp1": round(float(targets["rr_tp1"]), 3),
        "rr_tp2": round(float(targets["rr_tp2"]), 3),
        "rr_tp3": round(float(targets["rr_tp3"]), 3),
        "score": live_score,
        "quality": quality,
        "quality_note": "Movement Start V2 sıkışma/kırılım yapısı kontrollü canlı Premium yoluna yükseltildi.",
        "leverage": leverage,
        "trend_reason": (
            "15M + 1H ters değil; 4H eski yönü taşısa da erken kırılım profili ekstra şartlarla geçti."
            if not context.get("four_hour_ok")
            else "15M + 1H + 4H erken hareket yönüne karşı değil."
        ),
        "confirm_reason": f"V2 {stage} {base_score}/100 | Flow {flow_text}",
        "entry_reason": f"Sıkışma/kırılım erken girişi; anchor sapması %{drift:.2f}",
        "radar_reason": "NES/GPS/DOS tipi sıkışma sonrası hızlı breakout hareketlerini klasik 4H teyidini beklerken kaçırmamak için kontrollü Premium yol.",
        "early_breakout_version": VERSION,
        "early_breakout_stage": stage,
        "early_breakout_base_score": base_score,
        "early_breakout_opposite_score": context.get("opposite_score"),
        "early_breakout_flow_score": flow_score,
        "early_breakout_flow_confirmed": bool(flow_info.get("confirmed")),
        "early_breakout_targeted_flow": bool(flow_info.get("targeted_live_query")),
        "early_breakout_core_count": context.get("core_count"),
        "early_breakout_exceptional": bool(context.get("exceptional")),
        "early_breakout_four_hour_ok": bool(context.get("four_hour_ok")),
        "volume_ratio": round(float(_sf(features.get("volume_ratio"), 0.0) or 0.0), 3),
    }
    _record(symbol, base_result, "PROMOTED", "LIVE_EARLY_BREAKOUT", context, flow_info, live_score, now)
    return candidate


def strong_direct_allowed(
    signal: Dict[str, Any],
    current_price: Any,
    base_validator: Callable[..., Any],
    profit_module: Any,
) -> bool:
    if str(signal.get("source") or "").upper() != SOURCE:
        return False
    if str(signal.get("signal_class") or "").upper() != "TRADE":
        return False
    if int(_sf(signal.get("score"), 0) or 0) < MIN_LIVE_SCORE:
        return False
    risk = _sf(signal.get("risk_percent"), 999.0) or 999.0
    if not (MIN_RISK_PERCENT <= risk <= MAX_RISK_PERCENT):
        return False
    try:
        result = base_validator(signal, current_price)
        ok = bool(result[0] if isinstance(result, tuple) else result)
    except Exception:
        return False
    if not ok:
        return False
    try:
        return bool(profit_module.cost_viability(signal).get("ok"))
    except Exception:
        return False


def make_trade_message_builder(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(signal: Dict[str, Any], current_price: Any = None, portfolio_risk: Any = None) -> str:
        if str(signal.get("source") or "").upper() != SOURCE:
            return original(signal, current_price=current_price, portfolio_risk=portfolio_risk)
        direction = str(signal.get("direction") or "").upper()
        icon = "🟢" if direction == "LONG" else "🔴"
        flow_score = signal.get("early_breakout_flow_score")
        flow_text = (
            f"✅ {flow_score}/100"
            if signal.get("early_breakout_flow_confirmed")
            else f"{flow_score}/100"
            if flow_score is not None
            else "—"
        )
        portfolio_text = "ALLOW"
        if isinstance(portfolio_risk, dict):
            if portfolio_risk.get("hard_block"):
                portfolio_text = "BLOCK"
            elif portfolio_risk.get("warnings") or portfolio_risk.get("warning") or portfolio_risk.get("soft_warning"):
                portfolio_text = "ALLOW ⚠️"
        return (
            "✅ İŞLEM GİRİŞİ — PREMIUM ERKEN HAREKET\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{icon} {direction} | {signal.get('symbol')}\n"
            f"⚡ Yapı: {signal.get('early_breakout_stage')} • V2 {signal.get('early_breakout_base_score')}/100\n"
            f"💰 Giriş: {strategy.format_price(float(signal['entry']))}\n"
            f"🎯 TP1: {strategy.format_price(float(signal['tp1']))}\n"
            f"🎯 TP2: {strategy.format_price(float(signal['tp2']))}\n"
            f"🎯 TP3: {strategy.format_price(float(signal['tp3']))}\n"
            f"🛑 SL: {strategy.format_price(float(signal['sl']))}\n\n"
            f"⭐ Premium skor: {signal.get('score')}/100 • {signal.get('quality')}\n"
            f"📊 5M hacim: {float(_sf(signal.get('volume_ratio'), 0.0) or 0.0):.2f}x\n"
            f"🧬 Order Flow: {flow_text}\n"
            f"📍 Anchor sapması: %{float(_sf(signal.get('zone_distance_percent'), 0.0) or 0.0):.2f}\n"
            f"🛡 Stop: %{float(_sf(signal.get('risk_percent'), 0.0) or 0.0):.2f}\n"
            f"🔧 {signal.get('leverage')} | Isolated\n"
            f"🛡 Portfolio: {portfolio_text}\n\n"
            "Not: Bu rota klasik 4H teyidini beklemeden yalnız güçlü sıkışma/kırılım yapılarında çalışır."
        )
    return wrapped


def make_candidate_duplicate_guard(original: Callable[..., Any]) -> Callable[..., Any]:
    """Prevent two live routes from claiming the same symbol+direction in one scan."""
    claimed = set()

    def wrapped(signal: Dict[str, Any], radar: bool = False) -> bool:
        if radar:
            return bool(original(signal, radar=True))
        if bool(original(signal, radar=False)):
            return True
        key = (
            str(signal.get("symbol") or "").upper(),
            str(signal.get("direction") or "").upper(),
        )
        if key in claimed:
            return True
        claimed.add(key)
        return False

    return wrapped


def finish() -> Dict[str, Any]:
    global _DIRTY
    state = _state()
    records = state.get("records") if isinstance(state.get("records"), list) else []
    decisions = Counter(str(row.get("decision") or "UNKNOWN") for row in records)
    reasons = Counter(str(row.get("reason") or "UNKNOWN") for row in records if str(row.get("decision")) == "REJECTED")
    promoted_by_stage = Counter(
        str(row.get("stage") or "UNKNOWN")
        for row in records
        if str(row.get("decision")) == "PROMOTED"
    )
    summary = {
        "version": VERSION,
        "records": len(records),
        "promoted": decisions.get("PROMOTED", 0),
        "rejected": decisions.get("REJECTED", 0),
        "promoted_by_stage": dict(promoted_by_stage),
        "top_reject_reasons": dict(reasons.most_common(12)),
        "extra_flow_queries_this_run": _EXTRA_FLOW_QUERIES,
    }
    state["summary"] = summary
    state["updated_at"] = int(time.time())
    state["version"] = VERSION
    if _DIRTY or not os.path.exists(_STATE_PATH):
        _atomic_save(_STATE_PATH, state)
        _DIRTY = False
    return summary
