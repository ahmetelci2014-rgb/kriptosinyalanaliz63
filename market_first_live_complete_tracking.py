"""Top-level Market First live wrapper with complete direction observability.

The existing live strategy, entry-plan engine and opportunity ledger are installed
first. This layer adds only diagnostics:
- cache candidate market data for direction-ledger reconstruction,
- persist every dual-direction decision that reaches decision_to_signal,
- follow hypothetical direction outcomes for six hours,
- mark which direction decisions became real Telegram trade signals,
- copy direction/entry-plan metadata into open-signals and trade-ledger records.

No score, signal, risk, TP, SL, liquidity or portfolio rule is changed here.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import market_first_live_entry_plan_tracking as base_tracking
import market_first_runner as runner
import market_first_direction_engine as direction_engine
import market_first_direction_ledger as direction_ledger

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

    original_analyze_candidate = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal
    original_send_trade = runner._send_trade
    original_save_diagnostics = runner._save_diagnostics

    runtime: Dict[str, Any] = {
        "ledger": direction_ledger.load(runner.bot),
        "candidate_cache": {},
    }
    direction_ledger.finalize_expired(runtime["ledger"], runner.bot.now_ts())

    def analyze_with_direction_tracking(*args, **kwargs):
        symbol = str(_arg(args, kwargs, "symbol", 0, "") or "")
        current_price = _sf(_arg(args, kwargs, "current_price", 5, 0.0))
        df5m = _arg(args, kwargs, "df5m", 2)
        if symbol and current_price > 0:
            runtime["candidate_cache"][symbol] = {
                "df5m": df5m,
                "df15m": _arg(args, kwargs, "df15m", 3),
                "df1h": _arg(args, kwargs, "df1h", 4),
                "current_price": current_price,
                "quote_volume_24h": _sf(_arg(args, kwargs, "quote_volume_24h", 6, 0.0)),
                "context": _arg(args, kwargs, "context", 7),
            }
            direction_ledger.update_symbol_market(
                runtime["ledger"], symbol, current_price, df5m, runner.bot.now_ts()
            )
        return original_analyze_candidate(*args, **kwargs)

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
        if runner._is_live_run():
            direction_ledger.save(runner.bot, runtime["ledger"], now)
        summary = direction_ledger.summary(runtime["ledger"], now)
        print(
            "DIRECTION LEDGER | total=", summary.get("total"),
            "| reversal=", summary.get("reversal_total"),
            "| long=", summary.get("selected_long"),
            "| short=", summary.get("selected_short"),
            "| no-selection=", summary.get("engine_no_selection"),
            "| real-entry=", summary.get("real_entry_signal_sent"),
        )
        return result

    runner.analyze_candidate = analyze_with_direction_tracking
    runner.decision_to_signal = decision_to_signal_with_direction_tracking
    runner._send_trade = send_trade_with_direction_tracking
    runner._save_diagnostics = save_diagnostics_with_direction_tracking


def main() -> None:
    install_complete_tracking()
    runner.run()


if __name__ == "__main__":
    main()
