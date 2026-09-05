"""Live Market First entry point with symmetric LONG/SHORT entry planning.

The existing market_first_live guards are installed first. This overlay then adds
pre-trade preparation, zone confirmation, chase protection and a shadow outcome
ledger without replacing the underlying strategy or its final safety gates.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import market_first_live as live
import market_first_runner as runner
import market_first_entry_plan as entry_plan
import market_first_opportunity_ledger as opportunity_ledger

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

    original_select_deep_scan = runner._select_deep_scan
    original_analyze_candidate = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal
    original_format_trade_message = runner._format_trade_message
    original_send_trade = runner._send_trade
    original_save_diagnostics = runner._save_diagnostics

    runtime: Dict[str, Any] = {
        "state": runner.bot.load_json_file(entry_plan.STATE_FILE, {"plans": {}}),
        "ledger": opportunity_ledger.load(runner.bot),
        "prep_sent": 0,
    }
    now0 = runner.bot.now_ts()
    entry_plan.prune_state(runtime["state"], now0)
    opportunity_ledger.finalize_expired(runtime["ledger"], now0)

    def persist_plan_state() -> None:
        if runner._is_live_run():
            runner.bot.save_json_file(entry_plan.STATE_FILE, runtime["state"])

    def persist_opportunity_state(now: int) -> None:
        if not runner._is_live_run():
            return
        opportunity_ledger.save(runner.bot, runtime["ledger"], now)
        opportunity_ledger.save_combined_summary(runner.bot, runtime["ledger"], now)

    def arg(args, kwargs, name: str, index: int, default=None):
        if name in kwargs:
            return kwargs.get(name)
        return args[index] if len(args) > index else default

    def select_with_active_plan_tracking(rows, sample_moves, state):
        selected = original_select_deep_scan(rows, sample_moves, state)
        limit = max(len(selected), 40)
        tracked = opportunity_ledger.prioritize_tracking_symbols(
            selected,
            rows,
            runtime["ledger"],
            runtime["state"],
            now=runner.bot.now_ts(),
            max_total=limit,
        )
        if tracked != list(selected):
            forced = [symbol for symbol in tracked if symbol not in selected]
            if forced:
                print("ENTRY PLAN TAKİP ÖNCELİĞİ:", forced[:12])
        return tracked

    def analyze_with_entry_plan(*args, **kwargs):
        decision, reason = original_analyze_candidate(*args, **kwargs)

        symbol = str(arg(args, kwargs, "symbol", 0, "") or "")
        df5m = arg(args, kwargs, "df5m", 2)
        current_price = float(arg(args, kwargs, "current_price", 5, 0.0) or 0.0)
        quote_volume = float(arg(args, kwargs, "quote_volume_24h", 6, 0.0) or 0.0)
        context = arg(args, kwargs, "context", 7)
        now = runner.bot.now_ts()

        # First update already-open preparation episodes. This intentionally runs
        # before registering a new episode so the current 5m candle cannot create
        # retroactive TP/SL hits before the preparation actually existed.
        if symbol and current_price > 0:
            opportunity_ledger.update_symbol_market(
                runtime["ledger"], symbol, current_price, df5m, now
            )

        # Existing READY/FAST/FOLLOWTHROUGH trades always keep priority.
        if isinstance(decision, Mapping) and bool(decision.get("trade_eligible")):
            return decision, reason
        if not symbol or current_price <= 0 or context is None:
            return decision, reason
        if runner._is_live_run() and runner.bot.has_open_same_symbol(symbol):
            return decision, reason

        plan, plan_reason = entry_plan.evaluate_entry_plan(
            symbol=symbol,
            df5m=df5m,
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
        score = int(plan.get("score") or 0)
        current_direction = str((decision or {}).get("direction") or "") if isinstance(decision, Mapping) else ""

        # Every actionable PREP/ENTRY is recorded even if Telegram's per-run prep
        # cap prevents a visible alert. This separates detection quality from
        # notification delivery quality.
        if status in {"PREP", "ENTRY"} and score >= MIN_PREP_SCORE:
            episode, created = opportunity_ledger.register_or_refresh_plan(
                runtime["ledger"], plan, now
            )
            if created and episode:
                print(
                    "ENTRY PLAN LEDGER YENİ:", symbol, direction,
                    "| status=", status,
                    "| score=", score,
                    "| prep=", plan.get("current_price"),
                )

        # If 1m acceleration currently points against the higher-TF plan, keep it
        # observational until the micro move stops fighting the planned direction.
        direction_conflict = bool(current_direction and current_direction != direction)

        if status == "PREP" and score >= MIN_PREP_SCORE:
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
                    opportunity_ledger.mark_prep_sent(runtime["ledger"], plan, now)
                    persist_plan_state()
                    persist_opportunity_state(now)
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
                    persist_plan_state()
                    persist_opportunity_state(now)
                    print("GİRİŞ KOVALAMA ENGELİ:", symbol, direction)
            return decision, reason

        if status != "ENTRY":
            return decision, reason

        # ENTRY means the plan itself has reached zone + 5m + volume + score
        # confirmation. Record this even if a 1m conflict prevents promotion.
        opportunity_ledger.mark_entry_condition(
            runtime["ledger"],
            plan,
            now,
            promoted=not direction_conflict,
            micro_conflict=direction_conflict,
        )
        if direction_conflict:
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

    def send_trade_with_entry_tracking(exchange: Any, signal: Dict[str, Any], ml_store: Dict[str, Any]) -> bool:
        sent = original_send_trade(exchange, signal, ml_store)
        if not bool((signal or {}).get("entry_plan_trade")):
            return sent

        now = runner.bot.now_ts()
        opportunity_ledger.mark_entry_send_result(runtime["ledger"], signal, sent, now)
        if sent:
            entry_plan.mark_entry_sent(runtime["state"], signal, now)
            persist_plan_state()
        persist_opportunity_state(now)
        print(
            "ENTRY PLAN İŞLEM TESLİMATI:",
            signal.get("symbol"), signal.get("direction"),
            "| sent=", sent,
        )
        return sent

    def save_diagnostics_with_opportunity(*args, **kwargs):
        result = original_save_diagnostics(*args, **kwargs)
        now = runner.bot.now_ts()
        closed = opportunity_ledger.finalize_expired(runtime["ledger"], now)
        persist_opportunity_state(now)
        summary = opportunity_ledger.ledger_summary(runtime["ledger"])
        print(
            "OPPORTUNITY LEDGER | total=", summary.get("total"),
            "| open=", summary.get("open"),
            "| prep telegram=", summary.get("telegram_prep_sent"),
            "| zone=", summary.get("zone_touched"),
            "| entry condition=", summary.get("entry_condition_met"),
            "| entry sent=", summary.get("entry_signal_sent"),
            "| TP1 before entry=", summary.get("tp1_before_entry_signal"),
            "| closed now=", closed,
        )
        return result

    runner._select_deep_scan = select_with_active_plan_tracking
    runner.analyze_candidate = analyze_with_entry_plan
    runner.decision_to_signal = decision_to_signal_with_entry_plan
    runner._format_trade_message = format_trade_message_with_entry_plan
    runner._send_trade = send_trade_with_entry_tracking
    runner._save_diagnostics = save_diagnostics_with_opportunity


def main() -> None:
    install_entry_plan()
    runner.run()


if __name__ == "__main__":
    main()
