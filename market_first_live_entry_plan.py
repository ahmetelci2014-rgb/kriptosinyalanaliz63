"""Live Market First entry point with symmetric LONG/SHORT entry planning.

The existing market_first_live guards are installed first. This overlay then adds
pre-trade preparation, zone confirmation and chase protection without replacing
the underlying strategy or its final safety gates.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import market_first_live as live
import market_first_runner as runner
import market_first_entry_plan as entry_plan

_INSTALLED = False
MAX_PREP_MESSAGES_PER_RUN = 4
MIN_PREP_SCORE = 64


def install_entry_plan() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Keep every existing Market First guard and fast/follow-through path.
    live.install_guards()

    original_analyze_candidate = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal
    original_format_trade_message = runner._format_trade_message

    runtime: Dict[str, Any] = {
        "state": runner.bot.load_json_file(entry_plan.STATE_FILE, {"plans": {}}),
        "prep_sent": 0,
    }
    entry_plan.prune_state(runtime["state"], runner.bot.now_ts())

    def save_state() -> None:
        if runner._is_live_run():
            runner.bot.save_json_file(entry_plan.STATE_FILE, runtime["state"])

    def arg(args, kwargs, name: str, index: int, default=None):
        if name in kwargs:
            return kwargs.get(name)
        return args[index] if len(args) > index else default

    def analyze_with_entry_plan(*args, **kwargs):
        decision, reason = original_analyze_candidate(*args, **kwargs)

        symbol = str(arg(args, kwargs, "symbol", 0, "") or "")
        current_price = float(arg(args, kwargs, "current_price", 5, 0.0) or 0.0)
        quote_volume = float(arg(args, kwargs, "quote_volume_24h", 6, 0.0) or 0.0)
        context = arg(args, kwargs, "context", 7)

        # Existing READY/FAST/FOLLOWTHROUGH trades always keep priority.
        if isinstance(decision, Mapping) and bool(decision.get("trade_eligible")):
            return decision, reason
        if not symbol or current_price <= 0 or context is None:
            return decision, reason
        if runner._is_live_run() and runner.bot.has_open_same_symbol(symbol):
            return decision, reason

        plan, plan_reason = entry_plan.evaluate_entry_plan(
            symbol=symbol,
            df5m=arg(args, kwargs, "df5m", 2),
            df15m=arg(args, kwargs, "df15m", 3),
            df1h=arg(args, kwargs, "df1h", 4),
            current_price=current_price,
            quote_volume_24h=quote_volume,
            context=context,
        )
        if not isinstance(plan, Mapping):
            return decision, reason

        status = str(plan.get("status") or "")
        direction = str(plan.get("direction") or "")
        current_direction = str((decision or {}).get("direction") or "") if isinstance(decision, Mapping) else ""
        now = runner.bot.now_ts()

        # If 1m acceleration currently points against the higher-TF plan, keep it
        # observational until the micro move stops fighting the planned direction.
        direction_conflict = bool(current_direction and current_direction != direction)

        if status == "PREP" and int(plan.get("score") or 0) >= MIN_PREP_SCORE:
            if (
                int(runtime.get("prep_sent") or 0) < MAX_PREP_MESSAGES_PER_RUN
                and entry_plan.should_emit_preparation(runtime["state"], plan, now)
            ):
                sent = runner._send(
                    entry_plan.format_preparation(plan),
                    delivery_key=f"ENTRY_PLAN:PREP:{symbol}:{direction}:{now // entry_plan.PREP_REPEAT_SECONDS}",
                )
                if sent:
                    runtime["prep_sent"] = int(runtime.get("prep_sent") or 0) + 1
                    save_state()
                    print(
                        "İŞLEM HAZIRLIĞI:", symbol, direction,
                        "| zone=", plan.get("zone_low"), plan.get("zone_high"),
                        "| score=", plan.get("score"),
                    )
            return decision, reason

        if status == "CHASED":
            if entry_plan.should_emit_chased(runtime["state"], plan, now):
                sent = runner._send(
                    entry_plan.format_chased(plan),
                    delivery_key=f"ENTRY_PLAN:CHASED:{symbol}:{direction}",
                )
                if sent:
                    save_state()
                    print("GİRİŞ KOVALAMA ENGELİ:", symbol, direction)
            return decision, reason

        if status != "ENTRY" or direction_conflict:
            return decision, reason

        promoted = entry_plan.promote_to_decision(decision, plan)
        print(
            "GİRİŞ BÖLGESİ TEYİDİ -> İŞLEM ADAYI:",
            symbol,
            direction,
            "| zone=", plan.get("zone_low"), plan.get("zone_high"),
            "| entry=", plan.get("current_price"),
            "| risk=", plan.get("risk_percent"),
            "| roomR=", plan.get("room_r"),
            "| score=", plan.get("score"),
        )
        return promoted, "OK"

    def decision_to_signal_with_entry_plan(decision):
        signal = original_decision_to_signal(decision)
        return entry_plan.decorate_signal(signal, decision)

    def format_trade_message_with_entry_plan(signal):
        text = original_format_trade_message(signal)
        if bool((signal or {}).get("entry_plan_trade")):
            text = text.replace("✅ İŞLEM FIRSATI", "✅ GİRİŞ UYGUN", 1)
            zone_low = float((signal or {}).get("entry_plan_zone_low") or 0.0)
            zone_high = float((signal or {}).get("entry_plan_zone_high") or 0.0)
            if zone_low > 0 and zone_high > 0:
                text += f"\n📍 Plan bölgesi: {zone_low:.10g} - {zone_high:.10g}"
            text += "\n🧭 15M+1H yönü ve 5M bölge teyidi birlikte sağlandı."
        return text

    runner.analyze_candidate = analyze_with_entry_plan
    runner.decision_to_signal = decision_to_signal_with_entry_plan
    runner._format_trade_message = format_trade_message_with_entry_plan


def main() -> None:
    install_entry_plan()
    runner.run()


if __name__ == "__main__":
    main()
