"""Simple Core V1 live runner.

This replaces the layered Premium admission maze with one live setup:
1H direction -> 15M support/resistance rejection -> 5M trigger.

The proven operational pieces in main.py are reused:
- OKX data access
- open-trade/result tracking
- trade ledger
- duplicate/cooldown/risk limits
- market guard
- portfolio conflict guard
- Telegram delivery

No exchange order is ever placed.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List
import time

import main as bot
import premium_crypto_profit_runner as telegram_helpers
from simple_core_strategy import SOURCE, VERSION, analyze_simple_trade

DIAGNOSTICS_FILE = "simple_core_diagnostics.json"


def _install_entry_only_telegram() -> None:
    if not getattr(bot.send_telegram, "_trade_only_wrapped", False):
        bot.send_telegram = telegram_helpers._make_trade_only_sender(bot.send_telegram)


def _save_diagnostics(
    scanned: int,
    reasons: Counter,
    candidates: List[Dict[str, Any]],
) -> None:
    payload = {
        "version": VERSION,
        "generated_at": bot.now_ts(),
        "scanned": int(scanned),
        "candidate_count": len(candidates),
        "rejection_counts": dict(reasons.most_common()),
        "top_candidates": [
            {
                "symbol": item.get("symbol"),
                "direction": item.get("direction"),
                "score": item.get("score"),
                "zone_distance_percent": item.get("zone_distance_percent"),
                "risk_percent": item.get("risk_percent"),
                "room_r": item.get("room_r"),
                "volume_5m": item.get("volume_5m"),
            }
            for item in candidates[:10]
        ],
    }
    bot.save_json_file(DIAGNOSTICS_FILE, payload)


def _send_selected(
    exchange: Any,
    signal: Dict[str, Any],
    market_status: Dict[str, Any],
) -> bool:
    current_price = bot.get_current_price(exchange, signal["symbol"])
    valid, reason = bot.is_entry_still_valid(signal, current_price)
    if not valid:
        print(signal["symbol"], "son kontrol elendi:", reason)
        return False

    portfolio_risk = bot.evaluate_portfolio_risk(
        symbol=signal["symbol"],
        direction=signal["direction"],
        source_bot="MAIN_MTF",
    )
    signal["portfolio_risk"] = portfolio_risk

    if portfolio_risk.get("hard_block", False):
        print(
            signal["symbol"],
            "portföy çakışması nedeniyle elendi:",
            portfolio_risk.get("block_reason"),
        )
        return False

    entry = bot.safe_float(signal.get("entry"))
    tp1 = bot.safe_float(signal.get("tp1"))
    entry_distance = None
    tp1_progress = None

    if entry is not None and entry > 0 and current_price is not None:
        entry_distance = abs(current_price - entry) / entry * 100.0

        if (
            tp1 is not None
            and signal["direction"] == "LONG"
            and tp1 > entry
        ):
            tp1_progress = (current_price - entry) / (tp1 - entry) * 100.0
        elif (
            tp1 is not None
            and signal["direction"] == "SHORT"
            and tp1 < entry
        ):
            tp1_progress = (entry - current_price) / (entry - tp1) * 100.0

    signal["sent_price"] = current_price
    signal["entry_distance_at_send_percent"] = (
        round(entry_distance, 4) if entry_distance is not None else None
    )
    signal["tp1_progress_at_send_percent"] = (
        round(tp1_progress, 4) if tp1_progress is not None else None
    )
    signal["market_guard_long_allowed"] = market_status.get("LONG")
    signal["market_guard_short_allowed"] = market_status.get("SHORT")
    signal["market_guard_reason"] = market_status.get("reason")

    message = bot.build_short_trade_message(
        signal=signal,
        current_price=current_price,
        portfolio_risk=portfolio_risk,
    )

    if not bot.send_telegram(message):
        return False

    bot.save_open_signal(signal)
    bot.mark_sent(signal, radar=False)
    bot.update_performance(
        signal["symbol"],
        "OPENED",
        direction=signal["direction"],
        source=signal.get("source"),
        entry=signal.get("entry"),
        score=signal.get("score"),
    )
    return True


def run() -> None:
    _install_entry_only_telegram()

    print(
        "SIMPLE CORE LIVE:",
        VERSION,
        "| 1H yön -> 15M destek/direnç -> 5M teyit",
        "| pending=YOK | 5M erken bağımsız trade=YOK | deneysel live=YOK",
    )

    bot.sync_open_signals_to_ledger()
    exchange = bot.get_exchange()

    bot.check_open_signals(exchange)
    bot.check_sl_after_follow(exchange)
    bot.check_post_expiry_follow(exchange)
    bot.check_tp3_post_follow(exchange)
    bot.maybe_send_open_summary(exchange)

    risk_mode = bot.risk_mode_active()
    scan_coins = bot.get_scan_coins(exchange)
    market_status = bot.get_market_direction_status(exchange)

    risky_open, reduced_open, total_open = bot.count_open_signal_risk()
    print(
        "Simple Core başlangıç | tarama=",
        len(scan_coins),
        "| açık=",
        total_open,
        "| riskli=",
        risky_open,
        "| TP1 takip=",
        reduced_open,
        "| risk mode=",
        risk_mode,
    )

    reasons: Counter = Counter()
    candidates: List[Dict[str, Any]] = []
    scanned = 0

    for symbol in scan_coins:
        try:
            if bot.has_open_same_symbol(symbol):
                reasons["OPEN_SYMBOL"] += 1
                continue
            if bot.has_recent_stop(symbol):
                reasons["STOP_COOLDOWN"] += 1
                continue
            if bot.has_recent_closed_signal(symbol):
                reasons["RECENT_CLOSED"] += 1
                continue

            current_price = bot.get_current_price(exchange, symbol)
            if current_price is None:
                reasons["NO_PRICE"] += 1
                continue

            df5m = bot.fetch_df(
                exchange,
                symbol,
                bot.RADAR_TIMEFRAME,
                bot.RADAR_LIMIT,
                min_len=220,
            )
            df15m = bot.fetch_df(
                exchange,
                symbol,
                bot.ENTRY_TIMEFRAME,
                bot.ENTRY_LIMIT,
                min_len=220,
            )
            df1h = bot.fetch_df(
                exchange,
                symbol,
                bot.CONFIRM_TIMEFRAME,
                bot.CONFIRM_LIMIT,
                min_len=220,
            )
            scanned += 1

            signal, reason = analyze_simple_trade(
                symbol,
                df5m,
                df15m,
                df1h,
                current_price,
            )
            if signal is None:
                reasons[reason] += 1
                continue

            direction = signal["direction"]
            if direction == "LONG" and not bot.ALLOW_LONG:
                reasons["LONG_DISABLED"] += 1
                continue
            if direction == "SHORT" and not bot.ALLOW_SHORT:
                reasons["SHORT_DISABLED"] += 1
                continue

            if not market_status.get(direction, True):
                reasons["MARKET_OPPOSED"] += 1
                continue

            valid, valid_reason = bot.is_entry_still_valid(signal, current_price)
            if not valid:
                reasons["SEND_GEOMETRY"] += 1
                print(symbol, "giriş elendi ->", valid_reason)
                continue

            if bot.is_duplicate(signal, radar=False):
                reasons["DUPLICATE"] += 1
                continue

            candidates.append(signal)
            print(
                "SIMPLE CORE ADAY:",
                symbol,
                direction,
                "score=",
                signal.get("score"),
                "zone=",
                signal.get("zone_distance_percent"),
                "risk=",
                signal.get("risk_percent"),
                "roomR=",
                signal.get("room_r"),
            )

            time.sleep(0.08)

        except Exception as exc:
            reasons["ERROR"] += 1
            print(symbol, "Simple Core analiz hatası:", exc)

    candidates.sort(
        key=lambda item: (
            item.get("score", 0),
            item.get("room_r", 0),
            -float(item.get("zone_distance_percent") or 999.0),
        ),
        reverse=True,
    )

    max_trade = (
        bot.RISK_MODE_MAX_TRADE_SIGNALS
        if risk_mode
        else bot.MAX_TRADE_SIGNALS_PER_RUN
    )

    risky_open, _, _ = bot.count_open_signal_risk()
    available_slots = max(0, bot.MAX_OPEN_SIGNALS - risky_open)
    allowed_count = min(max_trade, available_slots)
    selected = candidates[:allowed_count]

    _save_diagnostics(scanned, reasons, candidates)

    print(
        "SIMPLE CORE ÖZET | taranan=",
        scanned,
        "| aday=",
        len(candidates),
        "| seçilen=",
        len(selected),
        "| en çok eleme=",
        reasons.most_common(8),
    )

    sent = 0
    for signal in selected:
        if _send_selected(exchange, signal, market_status):
            sent += 1
            time.sleep(1)

    if not selected:
        print("Simple Core uygun işlem yok.")
    elif sent == 0:
        print("Simple Core aday vardı fakat son güvenlik kontrolünde gönderilmedi.")

    bot.maybe_send_daily_report()
    print("SIMPLE CORE tamamlandı | gönderilen=", sent, "| source=", SOURCE)


if __name__ == "__main__":
    run()
