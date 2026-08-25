"""Global Premium entry guard driven by recorded outcomes and live context.

The guard is intentionally conservative and fail-open for missing/stale reports.
It never closes an existing trade. It only decides whether a *new* signal may
be sent.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Callable, Dict, Optional

import premium_quality_layer as quality

VERSION = "PREMIUM_GLOBAL_QUALITY_GUARD_V2_2026_08_25"
REPORT_FILE = "profit_mode_report.json"
REPORT_MAX_AGE_SECONDS = 6 * 60 * 60
TIGHT_MIN_SCORE = 94

# Proven-bad route quarantine. These are statistical thresholds, not symbol or
# direction hard-codes. A route can be blocked only when its own ledger history
# is materially negative.
ROUTE_EDGE_MIN_SAMPLE = 20
ROUTE_EDGE_MAX_AVG_NET_R = -0.05
ROUTE_EDGE_MAX_PROFIT_FACTOR = 0.80
ROUTE_EDGE_MAX_STOP_RATE = 40.0

# New routes get a small probation sample. Three straight stops with no TP3 is
# enough to stop risking fresh capital while the shadow observers keep learning.
ROUTE_PROBATION_MIN_SAMPLE = 3
ROUTE_PROBATION_MAX_SAMPLE = 9
ROUTE_PROBATION_STOP_RATE = 80.0
ROUTE_PROBATION_MAX_TP3_RATE = 0.0

# When source+direction statistics exist, suppress only the damaged side rather
# than shutting down a healthy opposite direction.
DIRECTION_EDGE_MIN_SAMPLE = 10
DIRECTION_EDGE_MAX_AVG_NET_R = -0.10
DIRECTION_EDGE_MAX_PROFIT_FACTOR = 0.70
DIRECTION_EDGE_MAX_STOP_RATE = 50.0

# Send-time geometry. This catches both chasing and signals that have already
# moved materially toward their stop before Telegram delivery.
MAX_TP1_PROGRESS_BASE = 40.0
MAX_TP1_PROGRESS_FAST_ROUTES = 30.0
MAX_ADVERSE_R_AT_SEND = 0.25
FAST_ROUTES = {
    "EARLY_BREAKOUT_ENTRY",
    "BIG_MOVE_ENTRY",
    "REGIME_TRANSITION_ENTRY",
}

_REPORT_CACHE: Dict[str, Any] = {"loaded_at": 0, "data": {}}


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _load_report(now_ts: int) -> Dict[str, Any]:
    loaded_at = int(_REPORT_CACHE.get("loaded_at") or 0)
    if now_ts - loaded_at <= 60 and isinstance(_REPORT_CACHE.get("data"), dict):
        return _REPORT_CACHE.get("data") or {}
    try:
        with open(REPORT_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _REPORT_CACHE["loaded_at"] = int(now_ts)
    _REPORT_CACHE["data"] = data
    return data


def _metric_block_reason(metrics: Dict[str, Any], *, direction_level: bool = False) -> Optional[str]:
    sample = int(_sf(metrics.get("sample"), 0) or 0)
    net_r = _sf(metrics.get("net_r_after_costs"), 0.0) or 0.0
    avg_r = _sf(metrics.get("avg_net_r_after_costs"))
    pf = _sf(metrics.get("profit_factor_after_costs"))
    stop_rate = _sf(metrics.get("stop_rate_percent"), 0.0) or 0.0
    tp3_rate = _sf(metrics.get("tp3_rate_percent"), 0.0) or 0.0

    if direction_level:
        if (
            sample >= DIRECTION_EDGE_MIN_SAMPLE
            and net_r < 0
            and (
                (avg_r is not None and avg_r <= DIRECTION_EDGE_MAX_AVG_NET_R)
                or (pf is not None and pf <= DIRECTION_EDGE_MAX_PROFIT_FACTOR)
                or stop_rate >= DIRECTION_EDGE_MAX_STOP_RATE
            )
        ):
            return "SOURCE_DIRECTION_NEGATIVE_EDGE"
        return None

    if (
        sample >= ROUTE_EDGE_MIN_SAMPLE
        and net_r < 0
        and (
            (avg_r is not None and avg_r <= ROUTE_EDGE_MAX_AVG_NET_R)
            or (pf is not None and pf <= ROUTE_EDGE_MAX_PROFIT_FACTOR)
            or stop_rate >= ROUTE_EDGE_MAX_STOP_RATE
        )
    ):
        return "SOURCE_NEGATIVE_EDGE"

    if (
        ROUTE_PROBATION_MIN_SAMPLE <= sample <= ROUTE_PROBATION_MAX_SAMPLE
        and net_r < 0
        and stop_rate >= ROUTE_PROBATION_STOP_RATE
        and tp3_rate <= ROUTE_PROBATION_MAX_TP3_RATE
    ):
        return "SOURCE_PROBATION_FAILED"
    return None


def route_performance_context(source: str, direction: str, now_ts: int) -> Dict[str, Any]:
    source = str(source or "").upper()
    direction = str(direction or "").upper()
    if not source:
        return {"mode": "UNKNOWN", "reason": "SOURCE_YOK"}

    report = _load_report(now_ts)
    breakdown = report.get("live_source_breakdown") if isinstance(report.get("live_source_breakdown"), dict) else {}
    generated_at = int(_sf(breakdown.get("generated_at"), 0) or 0)
    age = max(0, int(now_ts) - generated_at) if generated_at > 0 else None
    if age is None or age > REPORT_MAX_AGE_SECONDS:
        return {
            "mode": "UNKNOWN",
            "reason": "SOURCE_REPORT_ESKI_VEYA_YOK",
            "age_seconds": age,
        }

    windows = breakdown.get("windows") if isinstance(breakdown.get("windows"), dict) else {}
    seven = windows.get("7d") if isinstance(windows.get("7d"), dict) else {}
    sources = seven.get("sources") if isinstance(seven.get("sources"), dict) else {}
    metrics = sources.get(source) if isinstance(sources.get(source), dict) else {}
    if not metrics:
        return {
            "mode": "UNKNOWN",
            "reason": "SOURCE_SAMPLE_YOK",
            "age_seconds": age,
        }

    by_direction = metrics.get("by_direction") if isinstance(metrics.get("by_direction"), dict) else {}
    direction_metrics = by_direction.get(direction) if isinstance(by_direction.get(direction), dict) else {}
    direction_reason = _metric_block_reason(direction_metrics, direction_level=True) if direction_metrics else None
    route_reason = _metric_block_reason(metrics, direction_level=False)
    reason = direction_reason or route_reason

    return {
        "mode": "BLOCK" if reason else "NORMAL",
        "reason": reason or "SOURCE_EDGE_NOT_SEVERELY_NEGATIVE",
        "window": "7d",
        "age_seconds": age,
        "source": source,
        "direction": direction,
        "metrics": metrics,
        "direction_metrics": direction_metrics,
    }


def entry_geometry_context(signal: Dict[str, Any], current_price: Any) -> Dict[str, Any]:
    direction = str(signal.get("direction") or "").upper()
    source = str(signal.get("source") or "").upper()
    entry = _sf(signal.get("entry"))
    tp1 = _sf(signal.get("tp1"))
    sl = _sf(signal.get("sl"))
    live = _sf(current_price)

    if direction not in {"LONG", "SHORT"} or not all(value and value > 0 for value in (entry, tp1, sl, live)):
        return {"mode": "UNKNOWN", "reason": "GEOMETRY_DATA_YETERSIZ"}

    assert entry is not None and tp1 is not None and sl is not None and live is not None
    target_distance = abs(tp1 - entry)
    risk_distance = abs(entry - sl)
    if target_distance <= 0 or risk_distance <= 0:
        return {"mode": "UNKNOWN", "reason": "GEOMETRY_DISTANCE_YETERSIZ"}

    if direction == "LONG":
        progress = (live - entry) / target_distance * 100.0
        adverse_r = max(0.0, (entry - live) / risk_distance)
    else:
        progress = (entry - live) / target_distance * 100.0
        adverse_r = max(0.0, (live - entry) / risk_distance)

    max_progress = MAX_TP1_PROGRESS_FAST_ROUTES if source in FAST_ROUTES else MAX_TP1_PROGRESS_BASE
    mode = "NORMAL"
    reason = "ENTRY_GEOMETRY_OK"
    if progress > max_progress:
        mode = "BLOCK"
        reason = "ENTRY_TOO_LATE"
    elif adverse_r > MAX_ADVERSE_R_AT_SEND:
        mode = "BLOCK"
        reason = "ENTRY_ALREADY_ADVERSE"

    return {
        "mode": mode,
        "reason": reason,
        "tp1_progress_percent": round(progress, 4),
        "adverse_r_at_send": round(adverse_r, 4),
        "max_tp1_progress_percent": max_progress,
        "max_adverse_r_at_send": MAX_ADVERSE_R_AT_SEND,
    }


def install(bot: Any) -> None:
    original: Callable[..., Any] = bot.is_entry_still_valid
    if getattr(original, "_premium_global_quality_wrapped", False):
        return

    def wrapped(signal: dict, current_price: Any):
        now = int(time.time())
        direction = str(signal.get("direction") or "").upper()
        source = str(signal.get("source") or "").upper()
        score = int(quality._sf(signal.get("score"), 0) or 0)
        health = quality.direction_health(direction, now)
        market = quality.market_outlook_context(direction, now)
        route = route_performance_context(source, direction, now)
        geometry = entry_geometry_context(signal, current_price)
        regime_mode = str(signal.get("regime_transition_mode") or "").upper()
        is_regime_reversal = bool(
            source == "REGIME_TRANSITION_ENTRY"
            and "REVERSAL" in regime_mode
            and score >= 98
        )

        signal["global_quality_guard_version"] = VERSION
        signal["direction_health"] = health
        signal["market_outlook_quality"] = market
        signal["source_performance_guard"] = route
        signal["entry_geometry_quality"] = geometry

        reason = None
        if route.get("mode") == "BLOCK":
            reason = f"Kaynak performans karantinası: {route.get('reason')}"
        elif health.get("mode") == "PAUSE" and not is_regime_reversal:
            reason = "Yön sağlığı: son işlemlerde stop kümesi, yeni aynı yön giriş geçici durduruldu"
        elif market.get("mode") == "BLOCK" and not is_regime_reversal:
            reason = "Market Outlook: 6H/24H güçlü şekilde ters yönde"
        elif geometry.get("mode") == "BLOCK":
            if geometry.get("reason") == "ENTRY_TOO_LATE":
                reason = (
                    "Giriş geç: fiyat TP1 yolunun %"
                    f"{geometry.get('tp1_progress_percent')} bölümünü sinyal gönderilmeden tamamladı"
                )
            else:
                reason = (
                    "Giriş bozuldu: fiyat sinyal gönderilmeden stop riskinin "
                    f"{geometry.get('adverse_r_at_send')}R kısmını tüketti"
                )
        elif (
            health.get("mode") == "TIGHT"
            or market.get("mode") == "TIGHT"
        ) and score < TIGHT_MIN_SCORE:
            reason = f"Kalite sıkı mod: skor {score} < {TIGHT_MIN_SCORE}"

        evidence = {
            "health": health,
            "market": market,
            "route_performance": route,
            "entry_geometry": geometry,
        }
        if reason:
            quality._record("GLOBAL_ENTRY", signal, "REJECT", reason, evidence, now)
            print(signal.get("symbol"), "PREMIUM GLOBAL QUALITY RED:", reason)
            return False, reason

        quality._record("GLOBAL_ENTRY", signal, "ALLOW", "ALLOW", evidence, now)
        return original(signal, current_price)

    wrapped._premium_global_quality_wrapped = True  # type: ignore[attr-defined]
    bot.is_entry_still_valid = wrapped
