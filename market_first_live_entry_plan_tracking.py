"""Market First live entry point with complete opportunity outcome tracking.

This wrapper keeps the existing LONG/SHORT entry-plan engine untouched, then adds
an observational ledger around it. It tracks preparation quality even when no real
ENTRY signal is eventually sent, while keeping hypothetical opportunity results
separate from actual trade-ledger P&L.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import market_first_live_entry_plan as base
import market_first_runner as runner
import market_first_entry_plan as entry_plan
import market_first_opportunity_ledger as opportunity_ledger

_INSTALLED = False
MIN_TRACK_SCORE = 64


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def install_tracking() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    base.install_entry_plan()

    original_select_deep_scan = runner._select_deep_scan
    original_analyze_candidate = runner.analyze_candidate
    original_send_trade = runner._send_trade
    original_save_diagnostics = runner._save_diagnostics

    startup_state = runner.bot.load_json_file(entry_plan.STATE_FILE, {"plans": {}})
    runtime: Dict[str, Any] = {
        "ledger": opportunity_ledger.load(runner.bot),
        "startup_state": startup_state if isinstance(startup_state, dict) else {"plans": {}},
    }
    opportunity_ledger.finalize_expired(runtime["ledger"], runner.bot.now_ts())

    def arg(args, kwargs, name: str, index: int, default=None):
        if name in kwargs:
            return kwargs.get(name)
        return args[index] if len(args) > index else default

    def load_plan_state() -> Dict[str, Any]:
        state = runner.bot.load_json_file(entry_plan.STATE_FILE, {"plans": {}})
        return state if isinstance(state, dict) else {"plans": {}}

    def save_summary(now: int) -> None:
        if not runner._is_live_run():
            return
        opportunity_ledger.save(runner.bot, runtime["ledger"], now)
        payload = opportunity_ledger.combined_summary(runner.bot, runtime["ledger"], now)

        episodes = runtime["ledger"].get("episodes", {})
        clean = [
            item for item in episodes.values()
            if isinstance(item, Mapping) and not item.get("exclude_from_clean_prep_stats")
        ] if isinstance(episodes, Mapping) else []
        resolved = [item for item in clean if item.get("resolved")]
        payload["entry_plan_clean"] = {
            "total": len(clean),
            "open": sum(1 for item in clean if not item.get("resolved")),
            "resolved": len(resolved),
            "telegram_prep_sent": sum(1 for item in clean if item.get("telegram_prep_sent")),
            "zone_touched": sum(1 for item in clean if item.get("zone_touched")),
            "entry_condition_met": sum(1 for item in clean if item.get("entry_condition_met")),
            "entry_signal_sent": sum(1 for item in clean if item.get("entry_signal_sent")),
            "tp1_reached": sum(1 for item in clean if item.get("tp1_at")),
            "tp2_reached": sum(1 for item in clean if item.get("tp2_at")),
            "tp3_reached": sum(1 for item in clean if item.get("tp3_at")),
            "sl_first": sum(1 for item in clean if item.get("first_decisive_event") == "SL_FIRST"),
            "tp1_first": sum(1 for item in clean if item.get("first_decisive_event") == "TP1_FIRST"),
            "tp1_before_entry_signal": sum(1 for item in clean if item.get("tp1_before_entry_signal")),
            "stop_then_recovery": sum(
                1 for item in clean
                if item.get("first_decisive_event") == "SL_FIRST" and item.get("tp1_at")
            ),
            "note": "Clean stats exclude preparation alerts that existed before this ledger started tracking.",
        }
        runner.bot.save_json_file(opportunity_ledger.SUMMARY_FILE, payload)

    def select_with_tracking(rows, sample_moves, market_state):
        selected = original_select_deep_scan(rows, sample_moves, market_state)
        plan_state = load_plan_state()
        limit = max(len(selected), 40)
        tracked = opportunity_ledger.prioritize_tracking_symbols(
            selected,
            rows,
            runtime["ledger"],
            plan_state,
            now=runner.bot.now_ts(),
            max_total=limit,
        )
        forced = [symbol for symbol in tracked if symbol not in selected]
        if forced:
            print("OPPORTUNITY TAKİP ÖNCELİĞİ:", forced[:12])
        return tracked

    def analyze_with_tracking(*args, **kwargs):
        symbol = str(arg(args, kwargs, "symbol", 0, "") or "")
        df5m = arg(args, kwargs, "df5m", 2)
        current_price = _sf(arg(args, kwargs, "current_price", 5, 0.0))
        now = runner.bot.now_ts()

        if symbol and current_price > 0:
            opportunity_ledger.update_symbol_market(
                runtime["ledger"], symbol, current_price, df5m, now
            )

        decision, reason = original_analyze_candidate(*args, **kwargs)

        context = arg(args, kwargs, "context", 7)
        if not symbol or current_price <= 0 or context is None:
            return decision, reason

        plan, _ = entry_plan.evaluate_entry_plan(
            symbol=symbol,
            df5m=df5m,
            df15m=arg(args, kwargs, "df15m", 3),
            df1h=arg(args, kwargs, "df1h", 4),
            current_price=current_price,
            quote_volume_24h=_sf(arg(args, kwargs, "quote_volume_24h", 6, 0.0)),
            context=context,
        )
        if not isinstance(plan, Mapping):
            return decision, reason

        status = str(plan.get("status") or "").upper()
        direction = str(plan.get("direction") or "").upper()
        score = int(_sf(plan.get("score")))
        if status not in {"PREP", "ENTRY"} or score < MIN_TRACK_SCORE:
            return decision, reason

        episode, created = opportunity_ledger.register_or_refresh_plan(
            runtime["ledger"], plan, now
        )
        if episode is None:
            return decision, reason

        zone_low = _sf(plan.get("zone_low"))
        zone_high = _sf(plan.get("zone_high"))
        if zone_low > 0 and zone_high > 0 and zone_low <= current_price <= zone_high:
            episode["zone_touched"] = True
            episode.setdefault("zone_touch_at", int(now))

        key = f"{symbol}:{direction}"
        startup_plans = runtime["startup_state"].get("plans", {}) if isinstance(runtime["startup_state"], Mapping) else {}
        legacy = startup_plans.get(key) if isinstance(startup_plans, Mapping) else None
        if created and isinstance(legacy, Mapping):
            legacy_at = int(legacy.get("last_prep_at") or legacy.get("updated_at") or 0)
            if legacy_at and legacy_at < now:
                episode["legacy_adopted"] = True
                episode["original_prep_at"] = legacy_at
                episode["tracking_started_at"] = int(now)
                episode["telegram_prep_sent"] = True
                episode["prep_message_at"] = legacy_at
                episode["exclude_from_clean_prep_stats"] = True

        plan_state = load_plan_state()
        current_state = (plan_state.get("plans") or {}).get(key) if isinstance(plan_state.get("plans"), Mapping) else None
        if isinstance(current_state, Mapping):
            last_prep = int(current_state.get("last_prep_at") or 0)
            if last_prep and abs(now - last_prep) <= 90:
                opportunity_ledger.mark_prep_sent(runtime["ledger"], plan, last_prep)

        if status == "ENTRY":
            promoted = bool(isinstance(decision, Mapping) and decision.get("entry_plan_trade"))
            opportunity_ledger.mark_entry_condition(
                runtime["ledger"], plan, now, promoted=promoted
            )

        if created:
            print(
                "OPPORTUNITY LEDGER YENİ:", symbol, direction,
                "| status=", status,
                "| score=", score,
                "| telegram=", episode.get("telegram_prep_sent"),
                "| legacy=", episode.get("legacy_adopted", False),
            )
        return decision, reason

    def send_trade_with_tracking(exchange: Any, signal: Dict[str, Any], ml_store: Dict[str, Any]) -> bool:
        sent = original_send_trade(exchange, signal, ml_store)
        if not bool((signal or {}).get("entry_plan_trade")):
            return sent

        now = runner.bot.now_ts()
        opportunity_ledger.mark_entry_send_result(runtime["ledger"], signal, sent, now)
        if sent and runner._is_live_run():
            state = load_plan_state()
            entry_plan.mark_entry_sent(state, signal, now)
            runner.bot.save_json_file(entry_plan.STATE_FILE, state)
        save_summary(now)
        print(
            "OPPORTUNITY ENTRY TESLİMATI:",
            signal.get("symbol"), signal.get("direction"), "| sent=", sent,
        )
        return sent

    def save_diagnostics_with_tracking(*args, **kwargs):
        result = original_save_diagnostics(*args, **kwargs)
        now = runner.bot.now_ts()
        closed = opportunity_ledger.finalize_expired(runtime["ledger"], now)
        save_summary(now)
        summary = opportunity_ledger.ledger_summary(runtime["ledger"])
        print(
            "OPPORTUNITY ÖZET | total=", summary.get("total"),
            "| open=", summary.get("open"),
            "| telegram prep=", summary.get("telegram_prep_sent"),
            "| zone touched=", summary.get("zone_touched"),
            "| entry condition=", summary.get("entry_condition_met"),
            "| entry sent=", summary.get("entry_signal_sent"),
            "| TP1 before entry=", summary.get("tp1_before_entry_signal"),
            "| stop->recovery=", summary.get("stop_then_recovery"),
            "| closed now=", closed,
        )
        return result

    runner._select_deep_scan = select_with_tracking
    runner.analyze_candidate = analyze_with_tracking
    runner._send_trade = send_trade_with_tracking
    runner._save_diagnostics = save_diagnostics_with_tracking


def main() -> None:
    install_tracking()
    runner.run()


if __name__ == "__main__":
    main()
