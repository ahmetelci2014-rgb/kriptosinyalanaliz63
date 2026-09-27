"""All-coins live runner for Day Trading Core V1.

The original core helpers remain the decision engine. This runner expands the
universe to every active linear OKX USDT perpetual with a valid ticker, scans
the complete universe, ranks all fully-qualified setups, then sends at most the
existing per-run signal limit. Low-liquidity contracts are still observed but
cannot become live Telegram trades.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict

import day_trading_core as core

VERSION = "DAY_TRADING_ALL_COINS_V1_2026_09_27"
MIN_LIVE_24H_QUOTE_VOLUME = 200_000.0


def _enable_full_universe() -> None:
    # _load_universe reads these globals at runtime.
    core.MAX_SCAN_COINS = 100_000
    core.MIN_24H_QUOTE_VOLUME = 0.0


def _rank_key(item: Dict[str, Any]) -> tuple:
    trade = item["trade"]
    risk = core._sf(trade.get("risk_percent"), 99.0)
    risk_sweet_spot = 1 if 0.60 <= risk <= 1.00 else 0
    return (
        int(trade.get("score") or 0),
        risk_sweet_spot,
        core._sf(item.get("quote_volume")),
    )


def run() -> None:
    _enable_full_universe()

    state = core._load_json(core.STATE_FILE, core._default_state())
    ledger = core._load_json(core.LEDGER_FILE, core._default_ledger())
    state["version"] = core.VERSION
    ledger["version"] = core.VERSION

    exchange = core._get_exchange()
    diagnostics = Counter()
    core._track_open(exchange, state, ledger)

    universe = core._load_universe(exchange)
    diagnostics["UNIVERSE_TOTAL"] = len(universe)
    print(f"DAY TRADING ALL COINS | scanning {len(universe)} active OKX USDT swaps")

    qualified: list[Dict[str, Any]] = []
    for row in universe:
        symbol = str(row["symbol"])
        ccxt_symbol = str(row["ccxt_symbol"])
        live_price = core._sf(row["last"])
        quote_volume = core._sf(row.get("quote_volume"))
        try:
            df2h = core._fetch_df(exchange, ccxt_symbol, "2h")
            direction, metrics2h = core._direction_2h(df2h)
            if direction is None:
                diagnostics["2H_NO_DIRECTION"] += 1
                continue

            df15 = core._fetch_df(exchange, ccxt_symbol, "15m")
            ok15, reason15, metrics15 = core._setup_15m(df15, direction)
            if not ok15:
                diagnostics[reason15] += 1
                continue

            df5 = core._fetch_df(exchange, ccxt_symbol, "5m")
            ok5, reason5, metrics5 = core._trigger_5m(df5, direction)
            if not ok5:
                diagnostics[reason5] += 1
                continue

            metrics = {**metrics2h, **metrics15, **metrics5, "quote_volume_24h": quote_volume}
            trade, trade_reason = core._build_trade(
                symbol=symbol,
                ccxt_symbol=ccxt_symbol,
                live_price=live_price,
                direction=direction,
                df15=df15,
                df5=df5,
                metrics=metrics,
            )
            if trade is None:
                diagnostics[trade_reason] += 1
                continue

            trade["score"] = core._score(metrics, core._sf(trade["risk_percent"]))
            if quote_volume < MIN_LIVE_24H_QUOTE_VOLUME:
                diagnostics["LIVE_LIQUIDITY_LOW"] += 1
                continue

            qualified.append({"trade": trade, "quote_volume": quote_volume})
            diagnostics["FULLY_QUALIFIED"] += 1
        except Exception as exc:
            diagnostics["DATA_OR_ANALYSIS_ERROR"] += 1
            print("Day trade all-coins scan error:", symbol, exc)

    qualified.sort(key=_rank_key, reverse=True)

    new_signals = 0
    for item in qualified:
        if new_signals >= core.MAX_NEW_SIGNALS_PER_RUN:
            break
        trade = item["trade"]
        symbol = str(trade["symbol"])
        direction = str(trade["direction"])

        allowed, risk_reason = core._can_open(state, direction)
        if not allowed:
            diagnostics[f"RISK:{risk_reason}"] += 1
            continue
        if not core._cooldown_ok(state, symbol, direction):
            diagnostics["COOLDOWN"] += 1
            continue
        if not core._send_trade(trade):
            diagnostics["TELEGRAM_SEND_FAILED"] += 1
            continue

        state.setdefault("open_trades", {})[trade["trade_id"]] = trade
        state.setdefault("last_signal", {})[f"{symbol}:{direction}"] = core._now()
        day = core._day_row(state)
        day["signals"] = int(day.get("signals") or 0) + 1
        ledger.setdefault("trades", {})[trade["trade_id"]] = dict(trade)
        new_signals += 1
        diagnostics["SIGNAL_SENT"] += 1
        print("DAY TRADE SENT:", symbol, direction, trade["score"], trade["risk_percent"])

    state["updated_at"] = core._now()
    ledger["updated_at"] = core._now()
    core._atomic_save(core.STATE_FILE, state)
    core._atomic_save(core.LEDGER_FILE, ledger)
    summary = core._build_summary(ledger, state)
    core._atomic_save(core.SUMMARY_FILE, summary)
    core._atomic_save(
        core.DIAGNOSTICS_FILE,
        {
            "version": VERSION,
            "core_version": core.VERSION,
            "generated_at": core._now(),
            "universe_mode": "ALL_ACTIVE_OKX_USDT_SWAPS",
            "scanned": len(universe),
            "fully_qualified": len(qualified),
            "new_signals": new_signals,
            "min_live_quote_volume": MIN_LIVE_24H_QUOTE_VOLUME,
            "rejections": dict(diagnostics.most_common()),
            "open_trades": len(state.get("open_trades") or {}),
            "today": dict(core._day_row(state)),
        },
    )
    print("DAY TRADING ALL COINS SUMMARY:", summary)


if __name__ == "__main__":
    run()
