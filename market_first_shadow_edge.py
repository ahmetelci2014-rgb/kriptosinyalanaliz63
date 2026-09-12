"""Adaptive background-evidence bridge for Market First live trades.

The live system already records a large amount of hypothetical/background data.
This module turns those observations into a conservative adaptive gate instead of
ignoring them or blindly relaxing the whole strategy.

Principles:
- use only first-touch TP1-vs-SL style metrics from existing ledgers,
- enable a small profit-target relaxation only when both long-run and recent
  background evidence are positive and the live conversion rate is abnormally low,
- apply that relaxation only to very strong entry-plan candidates,
- never bypass market direction, current-flow, duplicate, recent-stop, portfolio,
  open-slot or ML safety checks,
- automatically disable the relaxation if the background edge deteriorates.

This module never places exchange orders and never sends Telegram directly.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Optional, Tuple

import market_first_profit_quality_v1 as profit_quality
import market_first_runner as runner
import market_first_strategy as strategy
import market_first_target_engine as target_engine

VERSION = "MARKET_FIRST_SHADOW_EDGE_V1_2026_09_12"
STATE_FILE = "market_first_shadow_edge.json"

OPPORTUNITY_FILE = "market_first_opportunity_summary.json"
DAILY_FILE = "market_first_daily_report.json"
SWING_FILE = "market_first_swing_2h_summary.json"
DIRECTION_FILE = "market_first_direction_summary.json"

MIN_ENTRY_PLAN_SAMPLES = 500
MIN_ENTRY_PLAN_TP_FIRST_RATE = 0.65
MIN_RECENT_SAMPLES = 50
MIN_RECENT_TP_FIRST_RATE = 0.72
MIN_SWING_V2_SAMPLES = 180
MIN_SWING_V2_TP_FIRST_RATE = 0.58
MAX_REAL_SIGNAL_CONVERSION_RATE_FOR_RELAXATION = 0.15

MIN_REVERSAL_SAMPLES = 80
MAX_REVERSAL_TP_FIRST_RATE = 0.45

RELAX_MIN_SCORE = 90
RELAX_MAX_RISK_PERCENT = 0.90
RELAX_MIN_ROOM_R = 2.50
RELAX_MAX_EXTENSION_ATR_5M = 0.90
RELAX_MIN_VOLUME_RATIO = 0.65
RELAX_MIN_EXPECTED_MOVE_PERCENT = 1.35
RELAX_MIN_TARGET_R = 2.25

_INSTALLED = False
_PROFILE: Dict[str, Any] = {}
_RUN_COUNTS: Counter = Counter()
_RUN_RELAXED: list[Dict[str, Any]] = []


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


def _arg(args, kwargs, name: str, index: int, default=None):
    if name in kwargs:
        return kwargs.get(name)
    return args[index] if len(args) > index else default


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _ratio(good: Any, bad: Any) -> Tuple[int, float]:
    good_i = max(0, _si(good))
    bad_i = max(0, _si(bad))
    total = good_i + bad_i
    return total, (good_i / total if total else 0.0)


def build_profile(
    opportunity: Optional[Mapping[str, Any]] = None,
    daily: Optional[Mapping[str, Any]] = None,
    swing: Optional[Mapping[str, Any]] = None,
    direction: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    opportunity = opportunity if isinstance(opportunity, Mapping) else {}
    daily = daily if isinstance(daily, Mapping) else {}
    swing = swing if isinstance(swing, Mapping) else {}
    direction = direction if isinstance(direction, Mapping) else {}

    clean = opportunity.get("entry_plan_clean")
    clean = clean if isinstance(clean, Mapping) else {}
    entry_samples, entry_rate = _ratio(clean.get("tp1_first"), clean.get("sl_first"))

    daily_summary = daily.get("summary")
    daily_summary = daily_summary if isinstance(daily_summary, Mapping) else {}
    recent_samples, recent_rate = _ratio(
        daily_summary.get("background_tp_first"),
        daily_summary.get("background_sl_first"),
    )

    swing_samples, swing_rate = _ratio(
        swing.get("v2_tp1_first"),
        swing.get("v2_sl_first"),
    )

    reversal_samples, reversal_rate = _ratio(
        direction.get("reversal_tp1_first"),
        direction.get("reversal_sl_first"),
    )

    entry_condition_met = max(0, _si(clean.get("entry_condition_met")))
    real_signal_sent = max(0, _si(clean.get("entry_signal_sent")))
    conversion_rate = real_signal_sent / entry_condition_met if entry_condition_met else 0.0

    long_run_edge = (
        entry_samples >= MIN_ENTRY_PLAN_SAMPLES
        and entry_rate >= MIN_ENTRY_PLAN_TP_FIRST_RATE
    )
    recent_edge = (
        recent_samples >= MIN_RECENT_SAMPLES
        and recent_rate >= MIN_RECENT_TP_FIRST_RATE
    )
    swing_edge = (
        swing_samples >= MIN_SWING_V2_SAMPLES
        and swing_rate >= MIN_SWING_V2_TP_FIRST_RATE
    )
    conversion_gap = (
        entry_condition_met >= 100
        and conversion_rate <= MAX_REAL_SIGNAL_CONVERSION_RATE_FOR_RELAXATION
    )

    relaxation_enabled = bool(long_run_edge and recent_edge and swing_edge and conversion_gap)
    reversal_negative = bool(
        reversal_samples >= MIN_REVERSAL_SAMPLES
        and reversal_rate <= MAX_REVERSAL_TP_FIRST_RATE
    )
    weighted_edge = (
        0.45 * entry_rate + 0.35 * recent_rate + 0.20 * swing_rate
        if min(entry_samples, recent_samples, swing_samples) > 0
        else 0.0
    )

    return {
        "version": VERSION,
        "daily_date": daily.get("date"),
        "entry_plan_samples": entry_samples,
        "entry_plan_tp_first_rate": round(entry_rate, 4),
        "recent_background_samples": recent_samples,
        "recent_background_tp_first_rate": round(recent_rate, 4),
        "swing_v2_samples": swing_samples,
        "swing_v2_tp_first_rate": round(swing_rate, 4),
        "entry_condition_met": entry_condition_met,
        "real_signal_sent": real_signal_sent,
        "real_signal_conversion_rate": round(conversion_rate, 4),
        "weighted_background_edge": round(weighted_edge, 4),
        "long_run_edge": long_run_edge,
        "recent_edge": recent_edge,
        "swing_edge": swing_edge,
        "conversion_gap": conversion_gap,
        "relaxation_enabled": relaxation_enabled,
        "reversal_samples": reversal_samples,
        "reversal_tp_first_rate": round(reversal_rate, 4),
        "reversal_negative": reversal_negative,
    }


def load_profile() -> Dict[str, Any]:
    return build_profile(
        _load(OPPORTUNITY_FILE),
        _load(DAILY_FILE),
        _load(SWING_FILE),
        _load(DIRECTION_FILE),
    )


def _aligned_candidate(decision: Mapping[str, Any], profile: Mapping[str, Any]) -> Tuple[bool, str]:
    if not bool(profile.get("relaxation_enabled")):
        return False, "BACKGROUND_EDGE_OFF"
    if not bool(decision.get("entry_plan_trade") or decision.get("tao_quality_bridge")):
        return False, "NOT_ENTRY_PLAN"

    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, "DIRECTION"
    if _si(decision.get("score")) < RELAX_MIN_SCORE:
        return False, "SCORE"

    risk = _sf(decision.get("risk_percent"), 999.0)
    if risk <= 0 or risk > RELAX_MAX_RISK_PERCENT:
        return False, "RISK"
    if _sf(decision.get("room_r")) < RELAX_MIN_ROOM_R:
        return False, "ROOM"
    if _sf(decision.get("extension_atr_5m"), 999.0) > RELAX_MAX_EXTENSION_ATR_5M:
        return False, "EXTENSION"

    volume = max(
        _sf(decision.get("volume_ratio_5m")),
        _sf(decision.get("volume_ratio_1m")),
        _sf(decision.get("volume_ratio")),
    )
    if volume < RELAX_MIN_VOLUME_RATIO and not bool(decision.get("tao_quality_bridge")):
        return False, "VOLUME"

    for timeframe in ("5m", "15m", "1h"):
        if str(decision.get(f"structure_{timeframe}") or "").upper() != direction:
            return False, f"STRUCTURE_{timeframe.upper()}"

    preferred = str(decision.get("market_preferred_direction") or "").upper()
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if preferred == opposite:
        return False, "MARKET_OPPOSITE"

    engine = decision.get("direction_engine")
    if isinstance(engine, Mapping) and bool(engine.get("reversal")):
        return False, "REVERSAL"
    return True, "OK"


def _directional_percent(direction: str, entry: float, target: float) -> float:
    if min(entry, target) <= 0:
        return 0.0
    raw = (target / entry - 1.0) * 100.0
    return raw if direction == "LONG" else -raw


def _price_from_percent(direction: str, entry: float, percent: float) -> float:
    sign = 1.0 if direction == "LONG" else -1.0
    return entry * (1.0 + sign * percent / 100.0)


def _relaxed_structural_target(
    *,
    direction: str,
    entry: float,
    risk_percent: float,
    df15m: Any,
    df1h: Any,
) -> Optional[Dict[str, Any]]:
    if direction not in {"LONG", "SHORT"} or entry <= 0 or risk_percent <= 0:
        return None
    try:
        s15 = strategy._structure(df15m, entry)
        s1h = strategy._structure(df1h, entry)
        two = target_engine._two_hour_context(df1h, entry)
    except Exception:
        return None
    if not isinstance(s15, Mapping) or not isinstance(s1h, Mapping) or not isinstance(two, Mapping):
        return None

    d15 = str(s15.get("direction") or "NEUTRAL").upper()
    d1h = str(s1h.get("direction") or "NEUTRAL").upper()
    d2h = str(two.get("direction") or "NEUTRAL").upper()
    if d15 != direction or d1h != direction or d2h != direction:
        return None

    if direction == "LONG":
        levels = [
            ("15M direnç", _sf(s15.get("range_high_72"))),
            ("1H direnç", _sf(s1h.get("range_high_72"))),
            ("2H direnç", _sf(two.get("range_high"))),
        ]
    else:
        levels = [
            ("15M destek", _sf(s15.get("range_low_72"))),
            ("1H destek", _sf(s1h.get("range_low_72"))),
            ("2H destek", _sf(two.get("range_low"))),
        ]

    candidates = []
    for source, level in levels:
        move = _directional_percent(direction, entry, level)
        target_r = move / risk_percent if risk_percent > 0 else 0.0
        if move < RELAX_MIN_EXPECTED_MOVE_PERCENT or target_r < RELAX_MIN_TARGET_R:
            continue
        candidates.append((move, source, level, target_r))
    if not candidates:
        return None

    raw_move, source, raw_level, _ = min(candidates, key=lambda item: item[0])
    move = min(raw_move, float(profit_quality.MAX_TARGET_MOVE_PERCENT))
    target = _price_from_percent(direction, entry, move)
    return {
        "profit_target": round(target, 12),
        "profit_target_percent": round(move, 4),
        "profit_target_r": round(move / risk_percent, 3),
        "profit_target_source": f"{source} • shadow-edge",
        "profit_raw_structure_level": round(raw_level, 12),
        "profit_raw_structure_percent": round(raw_move, 4),
        "profit_structure_15m": d15,
        "profit_structure_1h": d1h,
        "profit_structure_2h": d2h,
    }


def _save_state() -> Dict[str, Any]:
    payload = {
        "version": VERSION,
        "profile": dict(_PROFILE),
        "thresholds": {
            "relax_min_score": RELAX_MIN_SCORE,
            "relax_max_risk_percent": RELAX_MAX_RISK_PERCENT,
            "relax_min_room_r": RELAX_MIN_ROOM_R,
            "relax_max_extension_atr_5m": RELAX_MAX_EXTENSION_ATR_5M,
            "relax_min_volume_ratio": RELAX_MIN_VOLUME_RATIO,
            "relax_min_expected_move_percent": RELAX_MIN_EXPECTED_MOVE_PERCENT,
            "relax_min_target_r": RELAX_MIN_TARGET_R,
        },
        "run_counts": dict(_RUN_COUNTS),
        "relaxed_candidates": _RUN_RELAXED[-20:],
        "note": (
            "Background observations are hypothetical evidence, not realised PnL. "
            "The adaptive path only relaxes the structural target floor for strict aligned entry plans."
        ),
    }
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".shadow_edge.",
            suffix=".tmp",
            delete=False,
        ) as handle:
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
    global _INSTALLED, _PROFILE
    if _INSTALLED:
        return
    _INSTALLED = True
    _PROFILE = load_profile()

    original_analyze = runner.analyze_candidate
    original_quality_reason = profit_quality._quality_reason
    original_decision_to_signal = runner.decision_to_signal

    def analyze_with_shadow_edge(*args, **kwargs):
        result = original_analyze(*args, **kwargs)
        try:
            decision, reason = result
        except Exception:
            return result
        if not isinstance(decision, Mapping):
            return result

        out = dict(decision)
        out["shadow_edge_version"] = VERSION
        out["shadow_edge_weighted"] = _sf(_PROFILE.get("weighted_background_edge"))
        out["shadow_edge_recent_tp_first_rate"] = _sf(_PROFILE.get("recent_background_tp_first_rate"))

        supported, support_reason = _aligned_candidate(out, _PROFILE)
        out["shadow_edge_supported"] = bool(supported)
        out["shadow_edge_support_reason"] = support_reason
        _RUN_COUNTS[f"SUPPORT_{support_reason}"] += 1
        if not supported:
            return out, reason

        current_target = _sf(out.get("profit_target_percent"))
        current_r = _sf(out.get("profit_target_r"))
        if current_target >= profit_quality.MIN_EXPECTED_MOVE_PERCENT and current_r >= profit_quality.MIN_TARGET_R:
            _RUN_COUNTS["STANDARD_TARGET_ALREADY_OK"] += 1
            return out, reason

        direction = str(out.get("direction") or "").upper()
        entry = _sf(out.get("current_price")) or _sf(_arg(args, kwargs, "current_price", 5, 0.0))
        risk = _sf(out.get("risk_percent"))
        target = _relaxed_structural_target(
            direction=direction,
            entry=entry,
            risk_percent=risk,
            df15m=_arg(args, kwargs, "df15m", 3),
            df1h=_arg(args, kwargs, "df1h", 4),
        )
        if not isinstance(target, Mapping):
            _RUN_COUNTS["RELAX_TARGET_NOT_FOUND"] += 1
            return out, reason

        out.update(dict(target))
        out["shadow_edge_relaxed_profit_gate"] = True
        out["target_confidence"] = "YÜKSEK"
        out["technical_target"] = out.get("profit_target")
        out["technical_target_r"] = out.get("profit_target_r")
        out["technical_target_source"] = out.get("profit_target_source")
        out["expected_move_percent"] = out.get("profit_target_percent")
        out["expected_move_low_percent"] = round(_sf(out.get("profit_target_percent")) * 0.50, 4)
        out["expected_move_high_percent"] = out.get("profit_target_percent")
        _RUN_COUNTS["RELAX_TARGET_APPLIED"] += 1
        _RUN_RELAXED.append({
            "symbol": out.get("symbol"),
            "direction": out.get("direction"),
            "score": out.get("score"),
            "risk_percent": out.get("risk_percent"),
            "room_r": out.get("room_r"),
            "target_percent": out.get("profit_target_percent"),
            "target_r": out.get("profit_target_r"),
            "background_edge": out.get("shadow_edge_weighted"),
        })
        print(
            "SHADOW EDGE -> ADAPTİF HEDEF:", out.get("symbol"), out.get("direction"),
            "| score=", out.get("score"),
            "| risk=", out.get("risk_percent"),
            "| target%=", out.get("profit_target_percent"),
            "| targetR=", out.get("profit_target_r"),
        )
        return out, reason

    def quality_reason_with_shadow(decision: Mapping[str, Any]):
        ok, reason = original_quality_reason(decision)
        if ok:
            return ok, reason
        if reason not in {"EXPECTED_MOVE_BELOW_2P", "TARGET_R_BELOW_2_5"}:
            return ok, reason
        if not bool(decision.get("shadow_edge_relaxed_profit_gate")):
            return ok, reason
        if _sf(decision.get("profit_target_percent")) < RELAX_MIN_EXPECTED_MOVE_PERCENT:
            return False, "SHADOW_TARGET_TOO_SMALL"
        if _sf(decision.get("profit_target_r")) < RELAX_MIN_TARGET_R:
            return False, "SHADOW_TARGET_R_TOO_SMALL"

        # Re-run every unchanged Profit Quality rule while neutralising only the
        # two thresholds that this evidence-backed path is explicitly allowed to relax.
        replay = dict(decision)
        replay["profit_target_percent"] = max(
            _sf(replay.get("profit_target_percent")),
            float(profit_quality.MIN_EXPECTED_MOVE_PERCENT),
        )
        replay["profit_target_r"] = max(
            _sf(replay.get("profit_target_r")),
            float(profit_quality.MIN_TARGET_R),
        )
        replay_ok, replay_reason = original_quality_reason(replay)
        if replay_ok:
            _RUN_COUNTS["RELAX_QUALITY_ACCEPTED"] += 1
            return True, "OK_SHADOW_EDGE"
        return replay_ok, replay_reason

    def decision_to_signal_with_shadow(decision):
        signal = original_decision_to_signal(decision)
        if not isinstance(signal, Mapping) or not isinstance(decision, Mapping):
            return signal

        engine = decision.get("direction_engine")
        if (
            bool(_PROFILE.get("reversal_negative"))
            and isinstance(engine, Mapping)
            and bool(engine.get("reversal"))
        ):
            _RUN_COUNTS["BACKGROUND_REVERSAL_BLOCK"] += 1
            print(
                "SHADOW EDGE | ters-yön canlı işlem engellendi:",
                decision.get("symbol"),
                "background reversal TP-first=",
                _PROFILE.get("reversal_tp_first_rate"),
            )
            return None

        out = dict(signal)
        for key in (
            "shadow_edge_version",
            "shadow_edge_weighted",
            "shadow_edge_recent_tp_first_rate",
            "shadow_edge_supported",
            "shadow_edge_relaxed_profit_gate",
        ):
            if key in decision:
                out[key] = decision.get(key)
        return out

    runner.analyze_candidate = analyze_with_shadow_edge
    profit_quality._quality_reason = quality_reason_with_shadow
    runner.decision_to_signal = decision_to_signal_with_shadow


def finish() -> Dict[str, Any]:
    return _save_state()


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "profile": dict(_PROFILE),
        "relaxed_path": {
            "min_score": RELAX_MIN_SCORE,
            "max_risk_percent": RELAX_MAX_RISK_PERCENT,
            "min_room_r": RELAX_MIN_ROOM_R,
            "max_extension_atr_5m": RELAX_MAX_EXTENSION_ATR_5M,
            "min_volume_ratio": RELAX_MIN_VOLUME_RATIO,
            "min_expected_move_percent": RELAX_MIN_EXPECTED_MOVE_PERCENT,
            "min_target_r": RELAX_MIN_TARGET_R,
            "required_structure": "2H+1H+15M+5M aligned",
        },
        "run_counts": dict(_RUN_COUNTS),
        "exchange_orders": False,
    }
