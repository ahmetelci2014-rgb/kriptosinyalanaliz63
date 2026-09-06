"""Top-level Market First live wrapper with complete opportunity observability.

The existing live strategy, entry-plan engine and opportunity ledger are installed
first. This layer adds diagnostics plus an *observational* early 2H swing warning
built from the 1H candles already fetched for each deep-scan candidate.

Important: the 2H layer never creates a trade. Actual trades still require the
existing 15M/5M entry-plan or other Market First trade path plus all live-flow,
major-market, liquidity, cooldown and portfolio guards.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import market_first_live_entry_plan_tracking as base_tracking
import market_first_runner as runner
import market_first_direction_engine as direction_engine
import market_first_direction_ledger as direction_ledger
import market_first_entry_plan as entry_plan
import market_first_live_direction_guard as direction_guard
import market_first_swing_2h as swing_2h

_INSTALLED = False
MAX_SWING_ALERTS_PER_RUN = 2


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _arg(args, kwargs, name: str, index: int, default=None):
    if name in kwargs:
        return kwargs.get(name)
    return args[index] if len(args) > index else default


def _direction_metadata(decision: Mapping[str, Any]) -> Dict[str, Any]:
    diag = decision.get("direction_engine") if isinstance(decision, Mapping) else None
    if not isinstance(diag, Mapping):
        return {}
    long_diag = diag.get("long") if isinstance(diag.get("long"), Mapping) else {}
    short_diag = diag.get("short") if isinstance(diag.get("short"), Mapping) else {}
    return {
        "direction_engine_version": diag.get("version"),
        "direction_engine_reason": diag.get("reason"),
        "direction_engine_selected_direction": diag.get("selected_direction"),
        "direction_engine_current_direction": diag.get("current_direction"),
        "direction_engine_long_score": int(_sf(long_diag.get("score"))),
        "direction_engine_short_score": int(_sf(short_diag.get("score"))),
        "direction_engine_selected_score": int(_sf(diag.get("selected_score"))),
        "direction_engine_other_score": int(_sf(diag.get("other_score"))),
        "direction_engine_margin": int(_sf(diag.get("margin"))),
        "direction_engine_reversal": bool(diag.get("reversal")),
        "direction_engine_confirmations": int(_sf(diag.get("confirmations"))),
        "direction_engine_confirmation_flags": dict(diag.get("confirmation_flags") or {}),
        "direction_engine_structures": dict(diag.get("structures") or {}),
    }


def _patch_persistent_trade_metadata(signal: Mapping[str, Any]) -> None:
    if not isinstance(signal, Mapping):
        return
    metadata = {
        key: value
        for key, value in signal.items()
        if key.startswith("direction_engine_")
        or key in {
            "entry_type",
            "entry_plan_trade",
            "entry_plan_version",
            "entry_plan_zone_low",
            "entry_plan_zone_high",
            "entry_plan_ideal_entry",
            "ideal_entry",
            "zone_name",
        }
    }
    if not metadata:
        return

    trade_id = str(signal.get("trade_id") or "")
    symbol = str(signal.get("symbol") or "")
    direction = str(signal.get("direction") or "")
    source = str(signal.get("source") or "")

    def matches(record: Mapping[str, Any]) -> bool:
        if trade_id and str(record.get("trade_id") or "") == trade_id:
            return True
        return (
            str(record.get("symbol") or "") == symbol
            and str(record.get("direction") or "") == direction
            and str(record.get("source") or "") == source
            and not bool(record.get("closed"))
        )

    open_file = getattr(runner.bot, "OPEN_SIGNALS_FILE", "open_signals.json")
    opened = runner.bot.load_json_file(open_file, {})
    changed = False
    if isinstance(opened, dict):
        for record in opened.values():
            if isinstance(record, dict) and matches(record):
                record.update(metadata)
                changed = True
        if changed:
            runner.bot.save_json_file(open_file, opened)

    ledger_file = getattr(runner.bot, "TRADE_LEDGER_FILE", "trade_ledger.json")
    ledger = runner.bot.load_json_file(ledger_file, {})
    if not isinstance(ledger, dict):
        return
    containers = []
    if isinstance(ledger.get("trades"), dict):
        containers.append(ledger.get("trades"))
    else:
        containers.append(ledger)
    changed = False
    for container in containers:
        for record in container.values():
            if isinstance(record, dict) and matches(record):
                record.update(metadata)
                changed = True
    if changed:
        runner.bot.save_json_file(ledger_file, ledger)


def install_complete_tracking() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    base_tracking.install_tracking()

    original_select_deep_scan = runner._select_deep_scan
    original_analyze_candidate = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal
    original_send_trade = runner._send_trade
    original_save_diagnostics = runner._save_diagnostics

    runtime: Dict[str, Any] = {
        "ledger": direction_ledger.load(runner.bot),
        "candidate_cache": {},
        "swing_state": swing_2h.load_state(runner.bot),
        "swing_ledger": swing_2h.load_ledger(runner.bot),
        "swing_sent": 0,
    }
    now = runner.bot.now_ts()
    direction_ledger.finalize_expired(runtime["ledger"], now)
    swing_2h.finalize_expired(runtime["swing_ledger"], now)

    def select_with_swing_tracking(rows, sample_moves, state):
        selected = original_select_deep_scan(rows, sample_moves, state)
        return swing_2h.prioritize_active_symbols(
            selected,
            rows,
            runtime["swing_ledger"],
            runner.bot.now_ts(),
            max_total=max(len(selected), 40),
        )

    def analyze_with_direction_tracking(*args, **kwargs):
        symbol = str(_arg(args, kwargs, "symbol", 0, "") or "")
        current_price = _sf(_arg(args, kwargs, "current_price", 5, 0.0))
        quote_volume = _sf(_arg(args, kwargs, "quote_volume_24h", 6, 0.0))
        df5m = _arg(args, kwargs, "df5m", 2)
        df15m = _arg(args, kwargs, "df15m", 3)
        df1h = _arg(args, kwargs, "df1h", 4)
        raw_context = _arg(args, kwargs, "context", 7)
        context, _ = direction_guard.neutralize_breadth_conflict(raw_context)
        now = runner.bot.now_ts()

        if symbol and current_price > 0:
            runtime["candidate_cache"][symbol] = {
                "df5m": df5m,
                "df15m": df15m,
                "df1h": df1h,
                "current_price": current_price,
                "quote_volume_24h": quote_volume,
                "context": context,
            }
            direction_ledger.update_symbol_market(runtime["ledger"], symbol, current_price, df5m, now)
            swing_2h.update_symbol_market(runtime["swing_ledger"], symbol, current_price, df5m, now)

        decision, reason = original_analyze_candidate(*args, **kwargs)

        # Do not duplicate an ordinary entry-plan preparation. The swing warning
        # exists specifically for the earlier 2H+1H phase before 15M+1H alignment.
        if not symbol or current_price <= 0 or context is None:
            return decision, reason
        standard_plan, _ = entry_plan.evaluate_entry_plan(
            symbol=symbol,
            df5m=df5m,
            df15m=df15m,
            df1h=df1h,
            current_price=current_price,
            quote_volume_24h=quote_volume,
            context=context,
        )
        if isinstance(standard_plan, Mapping):
            return decision, reason

        swing_plan, swing_reason = swing_2h.evaluate_swing_preparation(
            symbol=symbol,
            df5m=df5m,
            df15m=df15m,
            df1h=df1h,
            current_price=current_price,
            quote_volume_24h=quote_volume,
            context=context,
        )
        if not isinstance(swing_plan, Mapping):
            return decision, reason

        alerted = False
        if (
            int(runtime.get("swing_sent") or 0) < MAX_SWING_ALERTS_PER_RUN
            and swing_2h.should_emit(runtime["swing_state"], swing_plan, now)
        ):
            alerted = bool(runner._send(
                swing_2h.format_preparation(swing_plan),
                delivery_key=(
                    f"SWING2H:{symbol}:{swing_plan.get('direction')}:"
                    f"{now // swing_2h.REPEAT_SECONDS}"
                ),
            ))
            if alerted:
                runtime["swing_sent"] = int(runtime.get("swing_sent") or 0) + 1
                swing_2h.mark_emitted(runtime["swing_state"], swing_plan, now)
                if runner._is_live_run():
                    swing_2h.save_state(runner.bot, runtime["swing_state"])
                print(
                    "2H SWING HAZIRLIĞI:", symbol, swing_plan.get("direction"),
                    "| score=", swing_plan.get("score"),
                    "| 2H/1H=", swing_plan.get("structure_2h"), swing_plan.get("structure_1h"),
                    "| 15M/5M=", swing_plan.get("structure_15m"), swing_plan.get("structure_5m"),
                )

        swing_2h.register_plan(runtime["swing_ledger"], swing_plan, now, alerted)
        return decision, reason

    def decision_to_signal_with_direction_tracking(decision):
        signal = original_decision_to_signal(decision)
        if not isinstance(decision, Mapping):
            return signal

        diag = decision.get("direction_engine")
        if not isinstance(diag, Mapping):
            return signal

        symbol = str(decision.get("symbol") or "")
        current_direction = str(decision.get("direction") or "").upper()
        selected = str(diag.get("selected_direction") or "").upper()
        cached = runtime["candidate_cache"].get(symbol) or {}
        reversal_plan = None

        if (
            selected in {"LONG", "SHORT"}
            and current_direction in {"LONG", "SHORT"}
            and selected != current_direction
            and cached
        ):
            plan, _ = direction_engine.build_direction_plan(
                symbol=symbol,
                direction=selected,
                df5m=cached.get("df5m"),
                df15m=cached.get("df15m"),
                df1h=cached.get("df1h"),
                current_price=_sf(cached.get("current_price")),
                quote_volume_24h=_sf(cached.get("quote_volume_24h")),
                context=cached.get("context"),
                direction_score=int(_sf(diag.get("selected_score"))),
            )
            if isinstance(plan, Mapping):
                reversal_plan = plan

        direction_ledger.register_decision(
            runtime["ledger"],
            decision=decision,
            diag=diag,
            now=runner.bot.now_ts(),
            reversal_plan=reversal_plan,
        )

        if isinstance(signal, dict):
            signal.update(_direction_metadata(decision))
            if bool(signal.get("entry_plan_trade")):
                signal.setdefault("entry_type", "MARKET_FIRST_ENTRY_PLAN")
        return signal

    def send_trade_with_direction_tracking(exchange: Any, signal: Dict[str, Any], ml_store: Dict[str, Any]) -> bool:
        sent = original_send_trade(exchange, signal, ml_store)
        now = runner.bot.now_ts()
        direction_ledger.mark_real_signal(runtime["ledger"], signal, sent, now)
        if sent:
            _patch_persistent_trade_metadata(signal)
        return sent

    def save_diagnostics_with_direction_tracking(*args, **kwargs):
        result = original_save_diagnostics(*args, **kwargs)
        now = runner.bot.now_ts()
        direction_ledger.finalize_expired(runtime["ledger"], now)
        swing_2h.finalize_expired(runtime["swing_ledger"], now)
        if runner._is_live_run():
            direction_ledger.save(runner.bot, runtime["ledger"], now)
            swing_2h.save_state(runner.bot, runtime["swing_state"])
            swing_2h.save_ledger(runner.bot, runtime["swing_ledger"], now)
        summary = direction_ledger.summary(runtime["ledger"], now)
        swing_summary = swing_2h.summary(runtime["swing_ledger"], now)
        print(
            "DIRECTION LEDGER | total=", summary.get("total"),
            "| reversal=", summary.get("reversal_total"),
            "| long=", summary.get("selected_long"),
            "| short=", summary.get("selected_short"),
            "| no-selection=", summary.get("engine_no_selection"),
            "| real-entry=", summary.get("real_entry_signal_sent"),
        )
        print(
            "2H SWING LEDGER | total=", swing_summary.get("total"),
            "| open=", swing_summary.get("open"),
            "| long/short=", swing_summary.get("long"), swing_summary.get("short"),
            "| tp1-first=", swing_summary.get("tp1_first"),
            "| sl-first=", swing_summary.get("sl_first"),
        )
        return result

    runner._select_deep_scan = select_with_swing_tracking
    runner.analyze_candidate = analyze_with_direction_tracking
    runner.decision_to_signal = decision_to_signal_with_direction_tracking
    runner._send_trade = send_trade_with_direction_tracking
    runner._save_diagnostics = save_diagnostics_with_direction_tracking


def main() -> None:
    install_complete_tracking()
    runner.run()


if __name__ == "__main__":
    main()
