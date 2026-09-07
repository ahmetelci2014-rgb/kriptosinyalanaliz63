"""Runtime overlay that attaches technical targets to every useful Market First stage.

Installed after the complete tracking stack, so it can enrich:
- raw selective EARLY observations,
- Entry Plan PREP/ENTRY plans,
- real trade signals,
- background 2H Swing plans,
without changing admission logic or risk guards.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import market_first_entry_plan as entry_plan
import market_first_runner as runner
import market_first_swing_2h as swing_2h
import market_first_target_engine as target_engine

VERSION = "MARKET_FIRST_TARGET_OVERLAY_V1_2026_09_07"
_INSTALLED = False


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _arg(args, kwargs, name: str, index: int, default=None):
    if name in kwargs:
        return kwargs.get(name)
    return args[index] if len(args) > index else default


def _attach(
    item: Mapping[str, Any],
    *,
    df15m: Any,
    df1h: Any,
    setup_kind: str,
    entry_override: float = 0.0,
) -> Dict[str, Any]:
    enriched = dict(item)
    target = target_engine.build_target_plan(
        direction=str(item.get("direction") or ""),
        entry=_sf(entry_override) or _sf(item.get("current_price")) or _sf(item.get("entry")),
        df15m=df15m,
        df1h=df1h,
        sl=_sf(item.get("sl")) or _sf(item.get("target_reference_sl")),
        tp1=_sf(item.get("tp1")) or _sf(item.get("target_reference_tp1")),
        tp2=_sf(item.get("tp2")) or _sf(item.get("target_reference_tp2")),
        tp3=_sf(item.get("tp3")) or _sf(item.get("target_reference_tp3")),
        market_preferred_direction=item.get("market_preferred_direction"),
        setup_kind=setup_kind,
    )
    if target:
        enriched.update(target)
    return enriched


def install_target_overlay() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_entry_eval = entry_plan.evaluate_entry_plan
    original_swing_eval = swing_2h.evaluate_swing_preparation
    original_swing_format = swing_2h.format_preparation
    original_analyze = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal

    runtime: Dict[str, Any] = {"frames": {}}

    def entry_eval_with_target(*args, **kwargs):
        plan, reason = original_entry_eval(*args, **kwargs)
        if not isinstance(plan, Mapping):
            return plan, reason
        enriched = _attach(
            plan,
            df15m=_arg(args, kwargs, "df15m", 2),
            df1h=_arg(args, kwargs, "df1h", 3),
            setup_kind="ENTRY_PLAN",
        )
        return enriched, reason

    def swing_eval_with_target(*args, **kwargs):
        plan, reason = original_swing_eval(*args, **kwargs)
        if not isinstance(plan, Mapping):
            return plan, reason
        enriched = _attach(
            plan,
            df15m=_arg(args, kwargs, "df15m", 2),
            df1h=_arg(args, kwargs, "df1h", 3),
            setup_kind="SWING_2H",
        )
        return enriched, reason

    def swing_format_with_target(plan: Mapping[str, Any]) -> str:
        text = original_swing_format(plan)
        target = _sf(plan.get("technical_target"))
        expected = _sf(plan.get("expected_move_percent"))
        if target <= 0 or expected <= 0:
            return text
        direction = str(plan.get("direction") or "").upper()
        move_word = "yükseliş" if direction == "LONG" else "düşüş"
        return (
            text
            + f"\n🎯 Teknik ana hedef: {runner.bot.format_price(target)}"
            + f"\n📐 Beklenen {move_word}: ~%{expected:.2f}"
            + f"\n🧩 Hedef kaynağı: {plan.get('technical_target_source')}"
        )

    def analyze_with_target(*args, **kwargs):
        symbol = str(_arg(args, kwargs, "symbol", 0, "") or "")
        df15m = _arg(args, kwargs, "df15m", 3)
        df1h = _arg(args, kwargs, "df1h", 4)
        current_price = _sf(_arg(args, kwargs, "current_price", 5, 0.0))
        if symbol:
            runtime["frames"][symbol] = {"df15m": df15m, "df1h": df1h}
            if len(runtime["frames"]) > 240:
                for key in list(runtime["frames"].keys())[: len(runtime["frames"]) - 240]:
                    runtime["frames"].pop(key, None)

        decision, reason = original_analyze(*args, **kwargs)
        if not isinstance(decision, Mapping):
            return decision, reason

        stage = str(decision.get("stage") or "").upper()
        if bool(decision.get("entry_plan_trade")):
            kind = "ENTRY_PLAN"
        elif stage == "EARLY":
            kind = "EARLY_MOVE"
        else:
            kind = "TRADE"
        enriched = _attach(
            decision,
            df15m=df15m,
            df1h=df1h,
            setup_kind=kind,
            entry_override=current_price,
        )
        return enriched, reason

    def decision_to_signal_with_target(decision):
        signal = original_decision_to_signal(decision)
        if not isinstance(signal, dict) or not isinstance(decision, Mapping):
            return signal

        symbol = str(signal.get("symbol") or decision.get("symbol") or "")
        cached = runtime["frames"].get(symbol) or {}
        kind = "ENTRY_PLAN" if bool(decision.get("entry_plan_trade")) else "TRADE"
        enriched = _attach(
            signal,
            df15m=cached.get("df15m"),
            df1h=cached.get("df1h"),
            setup_kind=kind,
            entry_override=_sf(signal.get("entry")),
        )
        # If fresh frame data is unexpectedly unavailable, preserve the target
        # already attached to the decision instead of dropping it.
        if not _sf(enriched.get("technical_target")):
            enriched.update(target_engine.target_fields(decision))
        return enriched

    entry_plan.evaluate_entry_plan = entry_eval_with_target
    swing_2h.evaluate_swing_preparation = swing_eval_with_target
    swing_2h.format_preparation = swing_format_with_target
    runner.analyze_candidate = analyze_with_target
    runner.decision_to_signal = decision_to_signal_with_target


def summary() -> dict:
    return {
        "version": VERSION,
        "targets": "15M_1H_2H_STRUCTURE_PLUS_EXISTING_RISK_GEOMETRY",
        "changes_trade_eligibility": False,
        "changes_guards": False,
    }
