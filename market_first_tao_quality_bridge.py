"""TAO-style MTF pullback bridge for Market First.

This bridge promotes a very narrow subset of PREP / PRE-ENTRY-shadow-quality
setups into the ordinary Market First trade pipeline.  It is designed around the
TAOUSDT profile observed on 2026-09-09:

- a strong recent volume expansion,
- 2H + 1H + 15M + 5M structure aligned,
- price back near the planned entry zone instead of extended,
- large structural room and >=2% realistic target,
- no strong opposite 1M/3M micro impulse,
- market context not strongly opposed.

The bridge never sends Telegram directly and never places exchange orders.  A
promoted setup still goes through the existing derivatives/ML enrichment,
Profit Quality gate, current-price validity, duplicate, recent-stop, portfolio
risk and open-slot checks.  Therefore Telegram still receives only the single
final ``KALİTELİ KRİPTO İŞLEM`` message.
"""
from __future__ import annotations

from collections import Counter
import math
from typing import Any, Dict, Mapping, Optional, Tuple

import market_first_entry_plan as entry_plan
import market_first_pre_entry_shadow as pre_entry_shadow
import market_first_profit_quality_v1 as profit_quality
import market_first_runner as runner
import market_first_strategy as strategy
import market_first_target_engine as target_engine

VERSION = "MARKET_FIRST_TAO_QUALITY_BRIDGE_V1_2026_09_09"
PROFILE = "TAO_MTF_PULLBACK"

MIN_SCORE = 88
MAX_ZONE_DISTANCE_PERCENT = 0.20
MIN_CURRENT_VOLUME_5M = 0.50
MIN_CURRENT_VOLUME_15M = 0.50
MIN_RECENT_PEAK_VOLUME_5M = 1.50
MIN_RECENT_PEAK_VOLUME_15M = 1.20
RECENT_VOLUME_BARS_5M = 12
RECENT_VOLUME_BARS_15M = 8
MAX_RISK_PERCENT = 1.25
MIN_ROOM_R = 4.00
MAX_EXTENSION_ATR_5M = 0.85
MIN_EXPECTED_MOVE_PERCENT = 2.00
MIN_TARGET_R = 2.50
MAX_OPPOSITE_MICRO_5M_PERCENT = 0.45

_INSTALLED = False
_RUN_COUNTS: Counter = Counter()
_RUN_PROMOTED: list[Dict[str, Any]] = []


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


def _recent_volume_peak(df: Any, bars: int) -> float:
    """Return peak closed-candle volume/median20 ratio without extra API calls."""
    try:
        frame = strategy._with_indicators(strategy._closed_frame(df, min_len=55))
    except Exception:
        frame = None
    if frame is None or not hasattr(frame, "tail") or "volume_ratio" not in frame.columns:
        return 0.0
    values = [_sf(value) for value in frame.tail(max(1, int(bars)))["volume_ratio"].tolist()]
    return max(values) if values else 0.0


def _micro_state(df1m: Any, current_price: float, direction: str) -> Tuple[bool, str, Dict[str, Any]]:
    """Allow a mild retest, but reject a fresh strong impulse against the setup."""
    try:
        acceleration = strategy._acceleration(df1m, current_price)
    except Exception:
        acceleration = None
    if not isinstance(acceleration, Mapping):
        return True, "NEUTRAL", {}

    micro_direction = str(acceleration.get("direction") or "").upper()
    move1 = _sf(acceleration.get("move_1m_percent"))
    move3 = _sf(acceleration.get("move_3m_percent"))
    move5 = _sf(acceleration.get("move_5m_percent"))
    evidence = {
        "micro_direction": micro_direction,
        "move_1m_percent": round(move1, 4),
        "move_3m_percent": round(move3, 4),
        "move_5m_percent": round(move5, 4),
        "micro_breakout": bool(acceleration.get("breakout")),
        "micro_same_direction": bool(acceleration.get("same_direction")),
    }
    if micro_direction == direction:
        return True, "ALIGNED", evidence

    opposite = "SHORT" if direction == "LONG" else "LONG"
    if micro_direction != opposite:
        return True, "NEUTRAL", evidence

    strong_opposite = (
        abs(move5) >= MAX_OPPOSITE_MICRO_5M_PERCENT
        or (bool(acceleration.get("breakout")) and bool(acceleration.get("same_direction")))
    )
    return (not strong_opposite), ("STRONG_OPPOSITE" if strong_opposite else "MILD_PULLBACK"), evidence


def evaluate_profile(
    plan: Mapping[str, Any] | None,
    *,
    df1m: Any,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Evaluate the strict TAO profile symmetrically for LONG and SHORT."""
    if not isinstance(plan, Mapping):
        return False, "NO_PLAN", {}
    if str(plan.get("status") or "").upper() != "PREP":
        return False, "NOT_PREP", {}

    shadow_ok, shadow_reason = pre_entry_shadow.qualifies(plan)
    if not shadow_ok:
        return False, f"SHADOW_{shadow_reason}", {}

    direction = str(plan.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, "DIRECTION", {}
    if _si(plan.get("score")) < MIN_SCORE:
        return False, "SCORE", {}

    # TAO profile is stricter than generic PRE-ENTRY: the micro structure itself
    # must already agree; NEUTRAL remains useful for shadow research but is not
    # promoted to a real Telegram trade here.
    if str(plan.get("structure_5m") or "").upper() != direction:
        return False, "5M_ALIGNMENT", {}
    if str(plan.get("structure_15m") or "").upper() != direction:
        return False, "15M_ALIGNMENT", {}
    if str(plan.get("structure_1h") or "").upper() != direction:
        return False, "1H_ALIGNMENT", {}

    two = target_engine._two_hour_context(df1h, current_price)
    direction_2h = str((two or {}).get("direction") or "NEUTRAL").upper()
    if direction_2h != direction:
        return False, "2H_ALIGNMENT", {"structure_2h": direction_2h}

    preferred = str(plan.get("market_preferred_direction") or "").upper()
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if preferred == opposite:
        return False, "MARKET_OPPOSITE", {"structure_2h": direction_2h}

    zone_distance = _sf(plan.get("zone_distance_percent"), 999.0)
    if zone_distance > MAX_ZONE_DISTANCE_PERCENT:
        return False, "ZONE_DISTANCE", {"structure_2h": direction_2h}

    volume5 = _sf(plan.get("volume_ratio_5m"))
    volume15 = _sf(plan.get("volume_ratio_15m"))
    if volume5 < MIN_CURRENT_VOLUME_5M:
        return False, "CURRENT_VOLUME_5M", {"structure_2h": direction_2h}
    if volume15 < MIN_CURRENT_VOLUME_15M:
        return False, "CURRENT_VOLUME_15M", {"structure_2h": direction_2h}

    peak5 = _recent_volume_peak(df5m, RECENT_VOLUME_BARS_5M)
    peak15 = _recent_volume_peak(df15m, RECENT_VOLUME_BARS_15M)
    if peak5 < MIN_RECENT_PEAK_VOLUME_5M:
        return False, "NO_RECENT_VOLUME_BURST_5M", {"structure_2h": direction_2h, "recent_peak_volume_5m": peak5}
    if peak15 < MIN_RECENT_PEAK_VOLUME_15M:
        return False, "NO_RECENT_VOLUME_BURST_15M", {"structure_2h": direction_2h, "recent_peak_volume_5m": peak5, "recent_peak_volume_15m": peak15}

    entry = _sf(current_price) or _sf(plan.get("current_price"))
    sl = _sf(plan.get("sl"))
    if entry <= 0 or sl <= 0:
        return False, "RISK_DATA", {}
    if direction == "LONG" and sl >= entry:
        return False, "SL_SIDE", {}
    if direction == "SHORT" and sl <= entry:
        return False, "SL_SIDE", {}
    actual_risk_percent = abs(entry - sl) / entry * 100.0
    if actual_risk_percent <= 0 or actual_risk_percent > MAX_RISK_PERCENT:
        return False, "RISK", {"actual_risk_percent": actual_risk_percent}
    if _sf(plan.get("room_r")) < MIN_ROOM_R:
        return False, "ROOM", {"actual_risk_percent": actual_risk_percent}
    if _sf(plan.get("extension_atr_5m"), 999.0) > MAX_EXTENSION_ATR_5M:
        return False, "EXTENSION", {"actual_risk_percent": actual_risk_percent}

    micro_ok, micro_state, micro = _micro_state(df1m, entry, direction)
    if not micro_ok:
        return False, "MICRO_OPPOSITE", {"structure_2h": direction_2h, "micro_state": micro_state, **micro}

    target = profit_quality._profit_target(
        direction=direction,
        entry=entry,
        risk_percent=actual_risk_percent,
        df15m=df15m,
        df1h=df1h,
    )
    if not isinstance(target, Mapping):
        return False, "NO_2P_STRUCTURAL_TARGET", {
            "structure_2h": direction_2h,
            "recent_peak_volume_5m": round(peak5, 3),
            "recent_peak_volume_15m": round(peak15, 3),
            "actual_risk_percent": round(actual_risk_percent, 4),
            "micro_state": micro_state,
            **micro,
        }
    if _sf(target.get("profit_target_percent")) < MIN_EXPECTED_MOVE_PERCENT:
        return False, "EXPECTED_MOVE", dict(target)
    if _sf(target.get("profit_target_r")) < MIN_TARGET_R:
        return False, "TARGET_R", dict(target)

    evidence: Dict[str, Any] = {
        "profile": PROFILE,
        "structure_2h": direction_2h,
        "structure_1h": direction,
        "structure_15m": direction,
        "structure_5m": direction,
        "zone_distance_percent": round(zone_distance, 4),
        "current_volume_5m": round(volume5, 3),
        "current_volume_15m": round(volume15, 3),
        "recent_peak_volume_5m": round(peak5, 3),
        "recent_peak_volume_15m": round(peak15, 3),
        "actual_risk_percent": round(actual_risk_percent, 4),
        "room_r": round(_sf(plan.get("room_r")), 2),
        "extension_atr_5m": round(_sf(plan.get("extension_atr_5m")), 3),
        "micro_state": micro_state,
        **micro,
        **dict(target),
    }
    return True, "OK", evidence


def _decorate_promoted(decision: Mapping[str, Any], evidence: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(decision)
    out.update({
        "tao_quality_bridge": True,
        "quality_profile": PROFILE,
        "tao_structure_2h": evidence.get("structure_2h"),
        "tao_current_volume_5m": evidence.get("current_volume_5m"),
        "tao_current_volume_15m": evidence.get("current_volume_15m"),
        "tao_recent_peak_volume_5m": evidence.get("recent_peak_volume_5m"),
        "tao_recent_peak_volume_15m": evidence.get("recent_peak_volume_15m"),
        "tao_micro_state": evidence.get("micro_state"),
        "tao_bridge_version": VERSION,
        "risk_percent": evidence.get("actual_risk_percent", out.get("risk_percent")),
        "target_confidence": "YÜKSEK",
    })
    for key in (
        "profit_target", "profit_target_percent", "profit_target_r",
        "profit_target_source", "profit_raw_structure_level",
        "profit_raw_structure_percent", "profit_structure_15m",
        "profit_structure_1h", "profit_structure_2h",
    ):
        if key in evidence:
            out[key] = evidence.get(key)
    return out


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_analyze = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal
    original_format = runner._format_trade_message

    def analyze_with_tao_bridge(*args, **kwargs):
        decision, reason = original_analyze(*args, **kwargs)
        if isinstance(decision, Mapping) and bool(decision.get("trade_eligible")):
            return decision, reason

        symbol = str(_arg(args, kwargs, "symbol", 0, "") or "")
        current_price = _sf(_arg(args, kwargs, "current_price", 5, 0.0))
        context = _arg(args, kwargs, "context", 7)
        if not symbol or current_price <= 0 or context is None:
            return decision, reason
        if runner._is_live_run() and runner.bot.has_open_same_symbol(symbol):
            return decision, reason

        try:
            plan, _ = entry_plan.evaluate_entry_plan(
                symbol=symbol,
                df5m=_arg(args, kwargs, "df5m", 2),
                df15m=_arg(args, kwargs, "df15m", 3),
                df1h=_arg(args, kwargs, "df1h", 4),
                current_price=current_price,
                quote_volume_24h=_sf(_arg(args, kwargs, "quote_volume_24h", 6, 0.0)),
                context=context,
            )
            ok, profile_reason, evidence = evaluate_profile(
                plan,
                df1m=_arg(args, kwargs, "df1m", 1),
                df5m=_arg(args, kwargs, "df5m", 2),
                df15m=_arg(args, kwargs, "df15m", 3),
                df1h=_arg(args, kwargs, "df1h", 4),
                current_price=current_price,
            )
            _RUN_COUNTS[profile_reason] += 1
            if not ok:
                return decision, reason

            promoted = entry_plan.promote_to_decision(decision, plan)
            promoted = _decorate_promoted(promoted, evidence)
            _RUN_COUNTS["PROMOTED"] += 1
            _RUN_PROMOTED.append({
                "symbol": symbol,
                "direction": promoted.get("direction"),
                "score": promoted.get("score"),
                "expected_move_percent": promoted.get("profit_target_percent"),
                "target_r": promoted.get("profit_target_r"),
                "peak_volume_5m": promoted.get("tao_recent_peak_volume_5m"),
                "peak_volume_15m": promoted.get("tao_recent_peak_volume_15m"),
                "micro_state": promoted.get("tao_micro_state"),
            })
            print(
                "TAO QUALITY -> İŞLEM ADAYI:", symbol, promoted.get("direction"),
                "| score=", promoted.get("score"),
                "| 2H/1H/15M/5M=", promoted.get("direction"),
                "| peakVol5=", promoted.get("tao_recent_peak_volume_5m"),
                "| target%=", promoted.get("profit_target_percent"),
                "| targetR=", promoted.get("profit_target_r"),
            )
            return promoted, "TAO_QUALITY_BRIDGE"
        except Exception as exc:
            _RUN_COUNTS["ERROR"] += 1
            print("TAO QUALITY değerlendirme hatası:", symbol, type(exc).__name__, exc)
            return decision, reason

    def decision_to_signal_with_profile(decision):
        signal = original_decision_to_signal(decision)
        if not isinstance(signal, Mapping) or not isinstance(decision, Mapping):
            return signal
        if not bool(decision.get("tao_quality_bridge")):
            return signal
        out = dict(signal)
        for key in (
            "tao_quality_bridge", "quality_profile", "tao_structure_2h",
            "tao_current_volume_5m", "tao_current_volume_15m",
            "tao_recent_peak_volume_5m", "tao_recent_peak_volume_15m",
            "tao_micro_state", "tao_bridge_version",
        ):
            out[key] = decision.get(key)
        return out

    def format_with_profile(signal: Mapping[str, Any]) -> str:
        text = original_format(signal)
        if not bool((signal or {}).get("tao_quality_bridge")):
            return text
        return (
            text
            + "\n🧩 Profil: TAO MTF Pullback"
            + "\n⏱ Uyum: 2H + 1H + 15M + 5M"
            + f"\n🔊 Hacim izi: 5M {_sf(signal.get('tao_recent_peak_volume_5m')):.2f}x"
              f" | 15M {_sf(signal.get('tao_recent_peak_volume_15m')):.2f}x"
            + f"\n⚡ Mikro durum: {signal.get('tao_micro_state') or 'NEUTRAL'}"
        )

    runner.analyze_candidate = analyze_with_tao_bridge
    runner.decision_to_signal = decision_to_signal_with_profile
    runner._format_trade_message = format_with_profile


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "profile": PROFILE,
        "mode": "STRICT_PREP_TO_NORMAL_TRADE_PIPELINE",
        "symmetric": "LONG_SHORT",
        "min_score": MIN_SCORE,
        "timeframes": "2H+1H+15M+5M; 1M/3M strong-opposite guard; BTC/ETH/SOL 4H market context remains active",
        "max_zone_distance_percent": MAX_ZONE_DISTANCE_PERCENT,
        "min_current_volume_5m": MIN_CURRENT_VOLUME_5M,
        "min_current_volume_15m": MIN_CURRENT_VOLUME_15M,
        "min_recent_peak_volume_5m": MIN_RECENT_PEAK_VOLUME_5M,
        "min_recent_peak_volume_15m": MIN_RECENT_PEAK_VOLUME_15M,
        "max_risk_percent": MAX_RISK_PERCENT,
        "min_room_r": MIN_ROOM_R,
        "max_extension_atr_5m": MAX_EXTENSION_ATR_5M,
        "min_expected_move_percent": MIN_EXPECTED_MOVE_PERCENT,
        "min_target_r": MIN_TARGET_R,
        "run_counts": dict(_RUN_COUNTS),
        "promoted": _RUN_PROMOTED[-10:],
        "telegram": "SINGLE_FINAL_QUALITY_TRADE_ONLY",
        "exchange_orders": False,
    }
