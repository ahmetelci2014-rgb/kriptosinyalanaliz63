"""All-coins live runner for Day Trading Core V1.

The original core helpers remain the decision engine. This runner expands the
universe to every active linear OKX USDT perpetual with a valid ticker, scans
the complete universe, ranks all fully-qualified setups, then sends at most the
existing per-run signal limit. Low-liquidity contracts are still observed but
cannot become live Telegram trades.

The core 1.10x 5M volume check remains the preferred path, but it is no longer
a misleading single-point bottleneck. When core rejects only on 5M_VOLUME, this
runner evaluates the rest of the price-action trigger instead of stopping at
volume. Sub-1.10x volume can pass only above a hard 0.80x floor and only when
body, extension, EMA/RSI momentum and the real two-bar breakout all pass. The
review reason is written to diagnostics so we can see whether low-volume rows
were actually good price action or would have failed for another reason too.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Mapping, Tuple

import pandas as pd

import day_trading_core as core

VERSION = "DAY_TRADING_ALL_COINS_V2_2026_09_29_TRIGGER_REVIEW"
MIN_LIVE_24H_QUOTE_VOLUME = 200_000.0

# Normal core threshold is still 1.10x. Below that, price action must be clean.
# 0.80x is a hard floor; this is not a blanket removal of volume confirmation.
QUALITY_OVERRIDE_MIN_VOLUME_RATIO = 0.80
QUALITY_OVERRIDE_MIN_BODY_ATR = 0.22
QUALITY_OVERRIDE_MAX_BODY_ATR = core.MAX_5M_BODY_ATR
QUALITY_OVERRIDE_MAX_EXTENSION_ATR = core.MAX_5M_EXTENSION_ATR


def _enable_full_universe() -> None:
    # _load_universe reads these globals at runtime.
    core.MAX_SCAN_COINS = 100_000
    core.MIN_24H_QUOTE_VOLUME = 0.0


def _quality_volume_override_check(
    df5: pd.DataFrame,
    direction: str,
    metrics: Mapping[str, Any],
) -> Tuple[bool, str]:
    """Review a core 5M_VOLUME rejection against the remaining trigger rules.

    Returns (allowed, review_reason). The normal >=1.10x path never needs this
    helper. For 0.80x-1.10x, the candle must still satisfy body, extension,
    direction-specific momentum and the same two-bar breakout used by core.
    """
    volume_ratio = core._sf(metrics.get("volume_ratio_5m"))
    body_atr = core._sf(metrics.get("body_atr_5m"))
    extension = core._sf(metrics.get("extension_atr_5m"), 99.0)
    rsi = core._sf(metrics.get("rsi_5m"), 50.0)

    if volume_ratio < QUALITY_OVERRIDE_MIN_VOLUME_RATIO:
        return False, "BELOW_0_80_FLOOR"
    if volume_ratio >= core.MIN_5M_VOLUME_RATIO:
        return False, "NOT_LOW_VOLUME"
    if body_atr < QUALITY_OVERRIDE_MIN_BODY_ATR:
        return False, "WEAK_BODY"
    if body_atr > QUALITY_OVERRIDE_MAX_BODY_ATR:
        return False, "SPIKE_BODY"
    if extension > QUALITY_OVERRIDE_MAX_EXTENSION_ATR:
        return False, "LATE_EXTENSION"

    try:
        c = core._completed(df5)
        previous = df5.iloc[-4:-2]
        if previous.empty:
            return False, "NO_HISTORY"

        close = core._sf(c["close"])
        open_ = core._sf(c["open"])
        ema9 = core._sf(c["ema9"])
        ema20 = core._sf(c["ema20"])

        if direction == "LONG":
            momentum = close > open_ and close > ema9 > ema20 and 51.0 <= rsi <= 72.0
            breakout = close > core._sf(previous["high"].max())
        else:
            momentum = close < open_ and close < ema9 < ema20 and 28.0 <= rsi <= 49.0
            breakout = close < core._sf(previous["low"].min())

        if not momentum:
            return False, "MOMENTUM"
        if not breakout:
            return False, "NO_BREAK"
        return True, "PASS"
    except Exception:
        return False, "EVAL_ERROR"


def _quality_volume_override(
    df5: pd.DataFrame,
    direction: str,
    metrics: Mapping[str, Any],
) -> bool:
    allowed, _ = _quality_volume_override_check(df5, direction, metrics)
    return allowed


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
            if not ok5 and reason5 == "5M_VOLUME":
                override_ok, review_reason = _quality_volume_override_check(df5, direction, metrics5)
                diagnostics[f"5M_VOLUME_REVIEW:{review_reason}"] += 1
                if override_ok:
                    ok5 = True
                    reason5 = "OK"
                    metrics5 = {
                        **metrics5,
                        "trigger_mode": "LOW_VOLUME_PRICE_ACTION_PASS",
                        "volume_review_reason": review_reason,
                    }
                    diagnostics["5M_VOLUME_OVERRIDE_PASS"] += 1
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
            "normal_5m_volume_ratio": core.MIN_5M_VOLUME_RATIO,
            "low_volume_hard_floor": QUALITY_OVERRIDE_MIN_VOLUME_RATIO,
            "rejections": dict(diagnostics.most_common()),
            "open_trades": len(state.get("open_trades") or {}),
            "today": dict(core._day_row(state)),
        },
    )
    print("DAY TRADING ALL COINS SUMMARY:", summary)


if __name__ == "__main__":
    run()
