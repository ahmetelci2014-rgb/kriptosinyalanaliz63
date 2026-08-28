"""Market First V5 runner.

The decision order is intentionally simple:
1) read BTC/ETH/SOL + breadth,
2) decide market wind,
3) scan the whole OKX USDT perpetual universe,
4) rank fresh movers,
5) analyze only the best/rotating candidates,
6) reject extended moves,
7) send compact early/trade messages,
8) keep each radar alert alive until CONTINUE / LATE / DEAD.

No exchange order is placed.
"""
from __future__ import annotations

from collections import Counter
import math
import os
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

import main as bot
from telegram_delivery import send_telegram_once

from market_first_strategy import (
    MAJOR_WEIGHTS,
    VERSION,
    MarketContext,
    analyze_candidate,
    build_market_context,
    decision_to_signal,
    lifecycle_update,
    market_label,
)

STATE_FILE = "market_first_state.json"
DIAGNOSTICS_FILE = "market_first_diagnostics.json"
LIVE_BRANCH = "main"

MAX_DEEP_SCAN = 40
TOP_SAMPLE_MOVERS = 20
TOP_DAILY_MOVERS = 6
TOP_VOLUME = 4
ROTATION_COUNT = 10
MAX_EARLY_ALERTS_PER_RUN = 3
ALERT_COOLDOWN_SECONDS = 30 * 60
MAX_ALERT_STATE = 600

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def _is_live_run() -> bool:
    return str(os.getenv("GITHUB_REF_NAME") or "").strip() == LIVE_BRANCH


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _pct(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return (end / start - 1.0) * 100.0


def _load_state() -> Dict[str, Any]:
    data = bot.load_json_file(STATE_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", VERSION)
    data.setdefault("previous_prices", {})
    data.setdefault("active_alerts", {})
    data.setdefault("alert_history", {})
    data.setdefault("rotation_cursor", 0)
    return data


def _save_state(state: Dict[str, Any]) -> None:
    state["version"] = VERSION
    state["updated_at"] = bot.now_ts()
    bot.save_json_file(STATE_FILE, state)


def _is_okx_usdt_swap(market: Mapping[str, Any]) -> bool:
    if market.get("active") is False:
        return False
    if not (market.get("swap") or market.get("contract")):
        return False
    if market.get("linear") is False:
        return False
    if str(market.get("quote") or "").upper() != "USDT":
        return False
    if str(market.get("settle") or "USDT").upper() != "USDT":
        return False
    if market.get("expiry") not in (None, 0, ""):
        return False
    return True


def _quote_volume(ticker: Mapping[str, Any]) -> float:
    direct = _sf(ticker.get("quoteVolume"))
    if direct > 0:
        return direct
    base = _sf(ticker.get("baseVolume"))
    last = _sf(ticker.get("last") or ticker.get("close"))
    return base * last if base > 0 and last > 0 else 0.0


def _change_24h(ticker: Mapping[str, Any]) -> float:
    direct = _sf(ticker.get("percentage"))
    if direct:
        return direct
    last = _sf(ticker.get("last") or ticker.get("close"))
    open_price = _sf(ticker.get("open"))
    return _pct(open_price, last) if min(last, open_price) > 0 else 0.0


def _load_universe(exchange: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    by_label: Dict[str, Dict[str, Any]] = {}

    for market in markets.values():
        if not _is_okx_usdt_swap(market):
            continue
        base = str(market.get("base") or "").upper().strip()
        ccxt_symbol = str(market.get("symbol") or "").strip()
        if not base or not ccxt_symbol:
            continue

        label = f"{base}USDT"
        ticker = tickers.get(ccxt_symbol) or {}
        last = _sf(ticker.get("last") or ticker.get("close"))
        if last <= 0:
            continue

        row = {
            "symbol": label,
            "ccxt_symbol": ccxt_symbol,
            "price": last,
            "quote_volume": _quote_volume(ticker),
            "change_24h": _change_24h(ticker),
        }

        old = by_label.get(label)
        if old and _sf(old.get("quote_volume")) >= row["quote_volume"]:
            continue
        by_label[label] = row

    rows = list(by_label.values())
    return rows, by_label


def _breadth_and_sample_moves(
    rows: List[Dict[str, Any]],
    previous_prices: Mapping[str, Any],
) -> Tuple[float, float, Dict[str, float]]:
    liquid = sorted(
        rows,
        key=lambda row: _sf(row.get("quote_volume")),
        reverse=True,
    )[:100]

    sample_moves: Dict[str, float] = {}
    positive_sample = 0
    matched = 0
    positive_24h = 0
    counted_24h = 0

    for row in liquid:
        symbol = str(row["symbol"])
        price = _sf(row.get("price"))
        prev = _sf(previous_prices.get(symbol))
        if price > 0 and prev > 0:
            move = _pct(prev, price)
            sample_moves[symbol] = move
            matched += 1
            if move > 0:
                positive_sample += 1
        change_24h = _sf(row.get("change_24h"))
        counted_24h += 1
        if change_24h > 0:
            positive_24h += 1

    for row in rows:
        symbol = str(row["symbol"])
        if symbol in sample_moves:
            continue
        price = _sf(row.get("price"))
        prev = _sf(previous_prices.get(symbol))
        if price > 0 and prev > 0:
            sample_moves[symbol] = _pct(prev, price)

    breadth_5m = positive_sample / matched if matched >= 20 else 0.50
    breadth_24h = positive_24h / counted_24h if counted_24h else 0.50
    return breadth_5m, breadth_24h, sample_moves


def _fetch_major_payloads(
    exchange: Any,
    universe: Mapping[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    payloads: Dict[str, Dict[str, Any]] = {}
    for symbol in MAJOR_WEIGHTS:
        row = universe.get(symbol)
        if not row:
            continue
        current = _sf(row.get("price"))
        try:
            payloads[symbol] = {
                "current_price": current,
                "5m": bot.fetch_df(exchange, symbol, "5m", 100, min_len=70),
                "15m": bot.fetch_df(exchange, symbol, "15m", 100, min_len=70),
                "1h": bot.fetch_df(exchange, symbol, "1h", 100, min_len=70),
                "4h": bot.fetch_df(exchange, symbol, "4h", 100, min_len=70),
            }
        except Exception as exc:
            print("Major veri hatası:", symbol, exc)
    return payloads


def _select_deep_scan(
    rows: List[Dict[str, Any]],
    sample_moves: Mapping[str, float],
    state: Dict[str, Any],
) -> List[str]:
    selected: List[str] = []
    seen = set()

    def add(symbol: str) -> None:
        if symbol and symbol not in seen and len(selected) < MAX_DEEP_SCAN:
            seen.add(symbol)
            selected.append(symbol)

    if sample_moves:
        for row in sorted(
            rows,
            key=lambda item: abs(_sf(sample_moves.get(str(item["symbol"])))),
            reverse=True,
        )[:TOP_SAMPLE_MOVERS]:
            add(str(row["symbol"]))

    daily_limit = TOP_DAILY_MOVERS if sample_moves else TOP_DAILY_MOVERS + TOP_SAMPLE_MOVERS
    for row in sorted(
        rows,
        key=lambda item: abs(_sf(item.get("change_24h"))),
        reverse=True,
    )[:daily_limit]:
        add(str(row["symbol"]))

    for row in sorted(
        rows,
        key=lambda item: _sf(item.get("quote_volume")),
        reverse=True,
    )[:TOP_VOLUME]:
        add(str(row["symbol"]))

    ordered = sorted(str(row["symbol"]) for row in rows)
    if ordered:
        cursor = int(state.get("rotation_cursor") or 0) % len(ordered)
        for offset in range(ROTATION_COUNT):
            add(ordered[(cursor + offset) % len(ordered)])
        state["rotation_cursor"] = (cursor + ROTATION_COUNT) % len(ordered)

    selected = [symbol for symbol in selected if symbol not in MAJOR_WEIGHTS]
    return selected[:MAX_DEEP_SCAN]


def _alert_key(symbol: str, direction: str) -> str:
    return f"{symbol}:{direction}"


def _can_new_alert(state: Dict[str, Any], symbol: str, direction: str, now: int) -> bool:
    key = _alert_key(symbol, direction)
    history = state.setdefault("alert_history", {})
    last = int((history.get(key) or {}).get("at") or 0)
    active = state.setdefault("active_alerts", {}).get(key) or {}
    if active and str(active.get("status") or "") != "DEAD":
        return False
    return now - last >= ALERT_COOLDOWN_SECONDS


def _register_alert(
    state: Dict[str, Any],
    decision: Mapping[str, Any],
    now: int,
) -> None:
    key = _alert_key(str(decision["symbol"]), str(decision["direction"]))
    price = _sf(decision.get("current_price"))
    state.setdefault("active_alerts", {})[key] = {
        "symbol": str(decision["symbol"]),
        "direction": str(decision["direction"]),
        "first_at": now,
        "alert_price": price,
        "best_price": price,
        "status": "NEW",
        "market_label": str(decision.get("market_label") or ""),
        "market_regime": str(decision.get("market_regime") or ""),
        "score": int(decision.get("score") or 0),
    }
    state.setdefault("alert_history", {})[key] = {
        "at": now,
        "price": price,
        "score": int(decision.get("score") or 0),
    }


def _prune_state(state: Dict[str, Any], now: int) -> None:
    active = state.setdefault("active_alerts", {})
    for key, item in list(active.items()):
        first_at = int((item or {}).get("first_at") or 0)
        status = str((item or {}).get("status") or "")
        if status == "DEAD" and first_at and now - first_at > 12 * 60 * 60:
            active.pop(key, None)

    history = state.setdefault("alert_history", {})
    if len(history) > MAX_ALERT_STATE:
        ordered = sorted(
            history.items(),
            key=lambda kv: int((kv[1] or {}).get("at") or 0),
            reverse=True,
        )
        state["alert_history"] = dict(ordered[:MAX_ALERT_STATE])


def _send(text: str, delivery_key: Optional[str] = None) -> bool:
    if not _is_live_run():
        print("MARKET FIRST TEST ONLY | Telegram engellendi:\n", text)
        return True
    return send_telegram_once(
        message=text,
        telegram_token=TOKEN,
        chat_id=CHAT_ID,
        bot_key="MARKET_FIRST_V5",
        delivery_key=delivery_key,
    )


def _format_early_message(decision: Mapping[str, Any]) -> str:
    direction = str(decision["direction"])
    icon = "🟢" if direction == "LONG" else "🔴"
    market = str(decision.get("market_label") or "KARIŞIK")
    independent = " | bağımsız güçlü hareket" if decision.get("independent_move") else ""
    return (
        f"🚨 ERKEN HAREKET | {decision['symbol']}\n"
        f"{icon} {direction}\n"
        f"🌍 Piyasa: {market}{independent}\n"
        f"⚡ 1dk {decision['move_1m_percent']:+.2f}% | "
        f"3dk {decision['move_3m_percent']:+.2f}% | "
        f"5dk {decision['move_5m_percent']:+.2f}%\n"
        f"🔊 Hacim: {decision['volume_ratio_1m']:.2f}x\n"
        f"✅ Durum: ERKEN\n"
        f"⚠️ İşlem teyidi değildir."
    )


def _format_status_message(item: Mapping[str, Any], update: Mapping[str, Any]) -> str:
    status = str(update.get("status"))
    if status == "CONTINUE":
        label = "DEVAM EDİYOR"
        icon = "🟢"
    elif status == "LATE":
        label = "GEÇ KALINDI"
        icon = "🟠"
    else:
        label = "BİTTİ"
        icon = "⚫"

    return (
        f"{icon} {item.get('symbol')} | {label}\n"
        f"{item.get('direction')}\n"
        f"İlk uyarıdan: {float(update.get('favorable_percent') or 0):+.2f}%"
    )


def _format_trade_message(signal: Mapping[str, Any]) -> str:
    direction = str(signal["direction"])
    icon = "🟢" if direction == "LONG" else "🔴"
    return (
        f"✅ İŞLEM FIRSATI | {signal['symbol']}\n"
        f"{icon} {direction} | Piyasa: {signal.get('market_label') or '-'}\n"
        f"📍 Giriş: {bot.format_price(signal['entry'])}\n"
        f"🛑 SL: {bot.format_price(signal['sl'])} "
        f"(%{float(signal.get('risk_percent') or 0):.2f})\n"
        f"🎯 TP1: {bot.format_price(signal['tp1'])}\n"
        f"🎯 TP2: {bot.format_price(signal['tp2'])}\n"
        f"🎯 TP3: {bot.format_price(signal['tp3'])}\n"
        f"⚠️ Otomatik emir açılmaz."
    )


def _update_alert_lifecycle(
    state: Dict[str, Any],
    universe: Mapping[str, Dict[str, Any]],
    now: int,
) -> int:
    changed_count = 0
    active = state.setdefault("active_alerts", {})
    for key, item in list(active.items()):
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") == "DEAD":
            continue
        symbol = str(item.get("symbol") or "")
        row = universe.get(symbol)
        if not row:
            continue
        current_price = _sf(row.get("price"))
        first_at = int(item.get("first_at") or now)
        age_minutes = max(0.0, (now - first_at) / 60.0)
        update = lifecycle_update(
            direction=str(item.get("direction") or ""),
            alert_price=_sf(item.get("alert_price")),
            best_price=_sf(item.get("best_price")),
            current_price=current_price,
            current_status=str(item.get("status") or "NEW"),
            age_minutes=age_minutes,
        )
        item["best_price"] = update["best_price"]
        item["status"] = update["status"]
        item["last_price"] = current_price
        item["last_favorable_percent"] = update["favorable_percent"]
        item["best_favorable_percent"] = update["best_favorable_percent"]
        item["updated_at"] = now

        if update.get("changed") and update.get("status") in {"CONTINUE", "LATE", "DEAD"}:
            changed_count += 1
            _send(
                _format_status_message(item, update),
                delivery_key=f"{key}|{update['status']}",
            )

    return changed_count


def _candidate_frames(exchange: Any, symbol: str) -> Tuple[Any, Any, Any, Any]:
    df1m = bot.fetch_df(exchange, symbol, "1m", 90, min_len=45)
    df5m = bot.fetch_df(exchange, symbol, "5m", 110, min_len=70)
    df15m = bot.fetch_df(exchange, symbol, "15m", 110, min_len=70)
    df1h = bot.fetch_df(exchange, symbol, "1h", 110, min_len=70)
    return df1m, df5m, df15m, df1h


def _send_trade(exchange: Any, signal: Dict[str, Any]) -> bool:
    symbol = str(signal["symbol"])
    current_price = bot.get_current_price(exchange, symbol)
    if current_price is None:
        return False

    valid, reason = bot.is_entry_still_valid(signal, current_price)
    if not valid:
        print(symbol, "son giriş kontrolü elendi:", reason)
        return False

    if bot.is_duplicate(signal, radar=False):
        print(symbol, "duplicate engellendi")
        return False

    portfolio = bot.evaluate_portfolio_risk(
        symbol=symbol,
        direction=signal["direction"],
        source_bot="MARKET_FIRST_V5",
    )
    signal["portfolio_risk"] = portfolio
    if portfolio.get("hard_block", False):
        print(symbol, "portföy çakışması:", portfolio.get("block_reason"))
        return False

    signal["sent_price"] = current_price
    signal["entry_distance_at_send_percent"] = (
        abs(current_price - _sf(signal.get("entry"))) / _sf(signal.get("entry")) * 100.0
        if _sf(signal.get("entry")) > 0
        else None
    )

    if not _is_live_run():
        print(
            "MARKET FIRST TEST ONLY | canlı işlem mesajı engellendi:",
            symbol,
            signal.get("direction"),
            "score=",
            signal.get("score"),
        )
        return True

    if not _send(_format_trade_message(signal)):
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


def _save_diagnostics(
    context: MarketContext,
    rows: List[Dict[str, Any]],
    selected_symbols: List[str],
    reasons: Counter,
    decisions: List[Dict[str, Any]],
    trade_candidates: List[Dict[str, Any]],
    lifecycle_changes: int,
) -> None:
    payload = {
        "version": VERSION,
        "generated_at": bot.now_ts(),
        "live_run": _is_live_run(),
        "market": context.to_dict(),
        "eligible_usdt_swaps": len(rows),
        "deep_scan_count": len(selected_symbols),
        "deep_scan_symbols": selected_symbols,
        "lifecycle_changes": lifecycle_changes,
        "rejection_counts": dict(reasons.most_common()),
        "top_decisions": decisions[:15],
        "trade_candidate_count": len(trade_candidates),
        "trade_candidates": [
            {
                "symbol": item.get("symbol"),
                "direction": item.get("direction"),
                "score": item.get("score"),
                "risk_percent": item.get("risk_percent"),
                "market_label": item.get("market_label"),
                "relative_strength_5m": item.get("relative_strength_5m"),
            }
            for item in trade_candidates[:10]
        ],
    }
    bot.save_json_file(DIAGNOSTICS_FILE, payload)


def run() -> None:
    print(
        "MARKET FIRST V5:",
        VERSION,
        "| önce BTC/ETH/SOL+breadth, sonra coin",
        "| live=",
        _is_live_run(),
    )

    state = _load_state()
    exchange = bot.get_exchange()

    if _is_live_run():
        bot.sync_open_signals_to_ledger()
        bot.check_open_signals(exchange)
        bot.check_sl_after_follow(exchange)
        bot.check_post_expiry_follow(exchange)
        bot.check_tp3_post_follow(exchange)

    rows, universe = _load_universe(exchange)
    previous_prices = state.get("previous_prices") or {}
    breadth_5m, breadth_24h, sample_moves = _breadth_and_sample_moves(
        rows,
        previous_prices,
    )

    major_payloads = _fetch_major_payloads(exchange, universe)
    context = build_market_context(
        major_payloads,
        breadth_5m=breadth_5m,
        breadth_24h=breadth_24h,
    )

    print(
        "PİYASA:",
        market_label(context),
        "| regime=",
        context.regime,
        "| score=",
        context.score,
        "| breadth5m=",
        round(context.breadth_5m * 100, 1),
        "| majors5m=",
        context.major_move_5m_percent,
    )

    now = bot.now_ts()
    lifecycle_changes = _update_alert_lifecycle(
        state,
        universe,
        now,
    )

    selected_symbols = _select_deep_scan(
        rows,
        sample_moves,
        state,
    )
    reasons: Counter = Counter()
    decisions: List[Dict[str, Any]] = []
    trade_candidates: List[Dict[str, Any]] = []

    alert_count = 0

    for symbol in selected_symbols:
        row = universe.get(symbol)
        if not row:
            continue
        try:
            current_price = _sf(row.get("price"))
            if current_price <= 0:
                reasons["NO_PRICE"] += 1
                continue

            df1m, df5m, df15m, df1h = _candidate_frames(exchange, symbol)
            decision, reason = analyze_candidate(
                symbol=symbol,
                df1m=df1m,
                df5m=df5m,
                df15m=df15m,
                df1h=df1h,
                current_price=current_price,
                quote_volume_24h=_sf(row.get("quote_volume")),
                context=context,
            )
            if decision is None:
                reasons[reason] += 1
                continue

            decisions.append(decision)

            if decision.get("stage") == "LATE":
                reasons["LATE_SKIP"] += 1
                print(
                    "GEÇ KALINDI:",
                    symbol,
                    decision.get("direction"),
                    "5m=",
                    decision.get("move_5m_percent"),
                    "extATR=",
                    decision.get("extension_atr_5m"),
                )
                continue

            signal = decision_to_signal(decision)
            if signal is not None:
                if not bot.has_open_same_symbol(symbol) and not bot.has_recent_stop(symbol):
                    trade_candidates.append(signal)
                else:
                    reasons["TRADE_COOLDOWN"] += 1

            if (
                decision.get("alert_eligible")
                and alert_count < MAX_EARLY_ALERTS_PER_RUN
                and _can_new_alert(state, symbol, str(decision["direction"]), now)
            ):
                _register_alert(state, decision, now)
                _send(_format_early_message(decision))
                alert_count += 1

            print(
                "ADAY:",
                symbol,
                decision.get("direction"),
                "stage=",
                decision.get("stage"),
                "score=",
                decision.get("score"),
                "market=",
                decision.get("market_label"),
                "rel5=",
                decision.get("relative_strength_5m"),
            )
            time.sleep(0.05)

        except Exception as exc:
            reasons["ERROR"] += 1
            print(symbol, "Market First analiz hatası:", type(exc).__name__, exc)

    decisions.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            _sf(item.get("relative_strength_5m")),
            -_sf(item.get("extension_atr_5m"), 999.0),
        ),
        reverse=True,
    )
    trade_candidates.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            _sf(item.get("relative_strength_5m")),
        ),
        reverse=True,
    )

    risk_mode = bot.risk_mode_active()
    max_new = bot.RISK_MODE_MAX_TRADE_SIGNALS if risk_mode else bot.MAX_TRADE_SIGNALS_PER_RUN
    risky_open, _, _ = bot.count_open_signal_risk()
    available_slots = max(0, bot.MAX_OPEN_SIGNALS - risky_open)
    selected_trades = trade_candidates[: min(max_new, available_slots)]

    for signal in selected_trades:
        _send_trade(exchange, signal)

    state["previous_prices"] = {
        str(row["symbol"]): _sf(row.get("price"))
        for row in rows
        if _sf(row.get("price")) > 0
    }
    state["last_market"] = context.to_dict()
    state["last_run"] = {
        "at": now,
        "eligible": len(rows),
        "deep_scanned": len(selected_symbols),
        "decisions": len(decisions),
        "early_alerts": alert_count,
        "trade_candidates": len(trade_candidates),
        "trades_selected": len(selected_trades),
        "lifecycle_changes": lifecycle_changes,
    }
    _prune_state(state, now)
    _save_state(state)
    _save_diagnostics(
        context,
        rows,
        selected_symbols,
        reasons,
        decisions,
        trade_candidates,
        lifecycle_changes,
    )

    print(
        "MARKET FIRST ÖZET | eligible=",
        len(rows),
        "| deep=",
        len(selected_symbols),
        "| aday=",
        len(decisions),
        "| erken=",
        alert_count,
        "| trade aday=",
        len(trade_candidates),
        "| seçilen=",
        len(selected_trades),
        "| lifecycle=",
        lifecycle_changes,
        "| eleme=",
        reasons.most_common(8),
    )


if __name__ == "__main__":
    run()
