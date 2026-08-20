"""Smart Recovery DCA1 live Telegram alerts.

Emir açmaz. Açık Premium MTF sinyallerinde tek DCA1 fırsatını arar ve
DCA sonrası tahmini ortalamaya dönüş / ana SL sonucunu bağımsız izler.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import strategy

VERSION = "SMART_RECOVERY_DCA1_V1_2026_08_20"
STATE_FILE = "smart_recovery_state.json"

DCA_SIZE_RATIO = 0.50
MIN_DCA_ADVERSE_R = 0.55
MAX_DCA_ADVERSE_R = 0.90
MIN_SIGNAL_AGE_MINUTES = 5
MAX_SIGNAL_AGE_HOURS = 6

TRACK_TIMEFRAME = "1m"
TRACK_LIMIT = 90


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return default if number != number else number
    except Exception:
        return default


def _empty_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "positions": {},
        "last_update": 0,
    }


def _load_state(bot) -> Dict[str, Any]:
    state = bot.load_json_file(STATE_FILE, _empty_state())
    if not isinstance(state, dict):
        state = _empty_state()
    state.setdefault("version", VERSION)
    state.setdefault("positions", {})
    state.setdefault("last_update", 0)
    if not isinstance(state["positions"], dict):
        state["positions"] = {}
    return state


def _save_state(bot, state: Dict[str, Any]) -> bool:
    state["version"] = VERSION
    state["last_update"] = bot.now_ts()
    return bot.save_json_file(STATE_FILE, state)


def _weighted_average_entry(entry: float, dca_price: float) -> float:
    """1.0x ilk notional + 0.5x DCA notional için yaklaşık ortalama."""
    initial_notional = 1.0
    dca_notional = DCA_SIZE_RATIO
    initial_qty = initial_notional / entry
    dca_qty = dca_notional / dca_price
    return (initial_notional + dca_notional) / (initial_qty + dca_qty)


def _adverse_r(direction: str, entry: float, sl: float, price: float) -> Optional[float]:
    risk = abs(entry - sl)
    if risk <= 0:
        return None

    direction = str(direction).upper()
    if direction == "LONG":
        return (entry - price) / risk
    if direction == "SHORT":
        return (price - entry) / risk
    return None


def _reversal_ok(df, direction: str, timeframe: str) -> Tuple[bool, str]:
    frame = strategy.add_indicators(df)
    if frame is None or len(frame) < 4:
        return False, f"{timeframe} veri yetersiz"

    row = frame.iloc[-2]
    prev = frame.iloc[-3]

    close = _safe_float(row.get("close"), 0.0) or 0.0
    open_price = _safe_float(row.get("open"), 0.0) or 0.0
    rsi = _safe_float(row.get("rsi"), 50.0) or 50.0
    macd_hist = _safe_float(row.get("macd_hist"), 0.0) or 0.0
    prev_macd_hist = _safe_float(prev.get("macd_hist"), 0.0) or 0.0
    close_power = strategy.close_power_percent(row)

    direction = str(direction).upper()

    if direction == "LONG":
        min_power = 55.0 if timeframe == "15M" else 58.0
        ok = (
            close > open_price
            and close_power >= min_power
            and rsi >= 40.0
            and macd_hist >= prev_macd_hist
        )
        return (
            ok,
            f"{timeframe} yeşil dönüş | RSI {rsi:.1f} | kapanış gücü %{close_power:.0f}",
        )

    if direction == "SHORT":
        max_power = 45.0 if timeframe == "15M" else 42.0
        ok = (
            close < open_price
            and close_power <= max_power
            and rsi <= 60.0
            and macd_hist <= prev_macd_hist
        )
        return (
            ok,
            f"{timeframe} kırmızı dönüş | RSI {rsi:.1f} | kapanış gücü %{close_power:.0f}",
        )

    return False, f"{timeframe} yön geçersiz"


def _format_price(bot, value: float) -> str:
    try:
        return bot.format_price(value)
    except Exception:
        return strategy.format_price(value)


def _event_key(trade_id: str, event: str) -> str:
    return f"SMART_RECOVERY:{trade_id}:{event}"


def _send_dca_alert(bot, record: Dict[str, Any]) -> bool:
    message = (
        "🟡 SMART RECOVERY • DCA1 UYGUN\n"
        f"{record['symbol']} • {record['direction']}\n\n"
        f"İlk giriş: {_format_price(bot, record['entry'])}\n"
        f"DCA1 bölgesi: {_format_price(bot, record['dca_price'])}\n"
        f"Tahmini yeni ortalama: {_format_price(bot, record['recovery_entry'])}\n"
        f"Ana SL: {_format_price(bot, record['sl'])}\n"
        f"Ters hareket: {record['adverse_r']:.2f}R\n\n"
        f"4H: {record['trend']} ✅\n"
        f"1H: {record['confirm']} ✅\n"
        f"15M: {record['reversal_15m']} ✅\n"
        f"5M: {record['reversal_5m']} ✅\n"
        "Market koruma: uygun ✅\n\n"
        "DCA1 boyutu: ilk pozisyonun en fazla %50'si.\n"
        "Tek DCA hakkı: DCA2 yok.\n"
        "Not: Bot emir açmaz; bu bir Telegram işlem yönetimi uyarısıdır."
    )
    return bool(
        bot.send_telegram(
            message,
            delivery_key=_event_key(record["trade_id"], "DCA1"),
        )
    )


def _send_result(bot, record: Dict[str, Any], result: str, price: float) -> bool:
    success = result == "RECOVERED"
    title = (
        "🔵 SMART RECOVERY • BAŞARILI"
        if success
        else "🔴 SMART RECOVERY • BAŞARISIZ"
    )
    detail = (
        "Fiyat DCA sonrası tahmini ortalamaya döndü."
        if success
        else "Ana SL, recovery ortalamasından önce görüldü."
    )

    message = (
        f"{title}\n"
        f"{record['symbol']} • {record['direction']}\n\n"
        f"İlk giriş: {_format_price(bot, record['entry'])}\n"
        f"DCA1: {_format_price(bot, record['dca_price'])}\n"
        f"Recovery ortalaması: {_format_price(bot, record['recovery_entry'])}\n"
        f"Sonuç fiyatı: {_format_price(bot, price)}\n\n"
        f"{detail}\n"
        "Bu takip ana Premium TP/SL kaydından bağımsızdır."
    )

    return bool(
        bot.send_telegram(
            message,
            delivery_key=_event_key(record["trade_id"], result),
        )
    )


def _track_existing(bot, exchange, state: Dict[str, Any]) -> bool:
    changed = False
    current_ts = bot.now_ts()

    for trade_id, record in list(state["positions"].items()):
        if str(record.get("status", "")).upper() != "ACTIVE":
            continue

        symbol = str(record.get("symbol") or "")
        direction = str(record.get("direction") or "").upper()
        recovery_entry = _safe_float(record.get("recovery_entry"))
        sl = _safe_float(record.get("sl"))

        if (
            not symbol
            or direction not in {"LONG", "SHORT"}
            or recovery_entry is None
            or sl is None
        ):
            continue

        df = bot.fetch_df(
            exchange,
            symbol,
            TRACK_TIMEFRAME,
            TRACK_LIMIT,
            min_len=5,
        )
        if df is None or df.empty:
            continue

        # Son satır açık mum olabilir; yalnız kapanmış 1M mumlar kullanılır.
        closed = df.iloc[:-1].copy()
        last_checked_at = int(
            record.get("last_checked_at")
            or record.get("sent_at")
            or 0
        )
        if last_checked_at > 0:
            closed = closed[
                (closed["time"] / 1000).astype(int)
                >= max(0, last_checked_at - 60)
            ]

        if closed.empty:
            continue

        outcome = None
        outcome_price = None

        for _, candle in closed.iterrows():
            high = float(candle["high"])
            low = float(candle["low"])

            if direction == "LONG":
                hit_recovery = high >= recovery_entry
                hit_sl = low <= sl
            else:
                hit_recovery = low <= recovery_entry
                hit_sl = high >= sl

            if hit_recovery and hit_sl:
                # Mum içinde sıra bilinmiyor; iyimser backtest yapmamak için SL-first.
                outcome = "FAILED"
                outcome_price = sl
                break
            if hit_sl:
                outcome = "FAILED"
                outcome_price = sl
                break
            if hit_recovery:
                outcome = "RECOVERED"
                outcome_price = recovery_entry
                break

        record["last_checked_at"] = current_ts
        changed = True

        if outcome:
            record["status"] = outcome
            record["closed_at"] = current_ts
            record["result_price"] = outcome_price
            _send_result(bot, record, outcome, outcome_price)

    return changed


def _scan_new(bot, exchange, state: Dict[str, Any]) -> bool:
    open_signals = bot.load_open_signals()
    if not open_signals:
        return False

    market_status = bot.get_market_direction_status(exchange)
    current_ts = bot.now_ts()
    changed = False

    for signal in open_signals.values():
        try:
            if bool(signal.get("tp1_hit", False)):
                continue

            symbol = str(signal.get("symbol") or "")
            direction = str(signal.get("direction") or "").upper()
            entry = _safe_float(signal.get("entry"))
            sl = _safe_float(signal.get("sl"))
            opened_at = int(signal.get("opened_at") or 0)
            trade_id = bot.build_trade_id(signal)

            if (
                not symbol
                or direction not in {"LONG", "SHORT"}
                or entry is None
                or sl is None
                or entry <= 0
                or sl <= 0
                or opened_at <= 0
                or trade_id in state["positions"]
            ):
                continue

            age_minutes = (current_ts - opened_at) / 60.0
            if age_minutes < MIN_SIGNAL_AGE_MINUTES:
                continue
            if age_minutes > MAX_SIGNAL_AGE_HOURS * 60:
                continue

            ticker = exchange.fetch_ticker(bot.to_okx_symbol(symbol))
            price = _safe_float(
                ticker.get("last")
                or ticker.get("close")
                or ticker.get("bid")
                or ticker.get("ask")
            )
            if price is None or price <= 0:
                continue

            adverse_r = _adverse_r(direction, entry, sl, price)
            if adverse_r is None:
                continue
            if not (
                MIN_DCA_ADVERSE_R
                <= adverse_r
                <= MAX_DCA_ADVERSE_R
            ):
                continue

            if not bool(market_status.get(direction, False)):
                continue

            df4h = bot.fetch_df(
                exchange,
                symbol,
                bot.TREND_TIMEFRAME,
                bot.TREND_LIMIT,
                min_len=220,
            )
            df1h = bot.fetch_df(
                exchange,
                symbol,
                bot.CONFIRM_TIMEFRAME,
                bot.CONFIRM_LIMIT,
                min_len=220,
            )
            df15 = bot.fetch_df(
                exchange,
                symbol,
                bot.ENTRY_TIMEFRAME,
                bot.ENTRY_LIMIT,
                min_len=220,
            )
            df5 = bot.fetch_df(
                exchange,
                symbol,
                bot.RADAR_TIMEFRAME,
                bot.RADAR_LIMIT,
                min_len=220,
            )

            trend, trend_reason, _ = strategy.get_4h_trend(df4h)
            confirm, confirm_reason, _ = strategy.get_1h_confirm(df1h)

            if not strategy.trend_supports_direction(
                direction,
                trend,
                confirm,
                strict=True,
            ):
                continue

            ok15, reason15 = _reversal_ok(df15, direction, "15M")
            ok5, reason5 = _reversal_ok(df5, direction, "5M")
            if not (ok15 and ok5):
                continue

            recovery_entry = _weighted_average_entry(entry, price)

            record = {
                "version": VERSION,
                "trade_id": trade_id,
                "symbol": symbol,
                "direction": direction,
                "entry": round(entry, 12),
                "sl": round(sl, 12),
                "dca_price": round(price, 12),
                "dca_size_ratio": DCA_SIZE_RATIO,
                "recovery_entry": round(recovery_entry, 12),
                "adverse_r": round(adverse_r, 4),
                "trend": trend,
                "trend_reason": trend_reason,
                "confirm": confirm,
                "confirm_reason": confirm_reason,
                "reversal_15m": reason15,
                "reversal_5m": reason5,
                "sent_at": current_ts,
                "last_checked_at": current_ts,
                "status": "ACTIVE",
            }

            if _send_dca_alert(bot, record):
                state["positions"][trade_id] = record
                changed = True

        except Exception as exc:
            print(
                signal.get("symbol"),
                "Smart Recovery DCA1 hatası:",
                exc,
            )

    return changed


def run(bot) -> None:
    """Önce eski DCA1 kayıtlarını izle, sonra yeni DCA1 fırsatlarını ara."""
    state = _load_state(bot)
    exchange = bot.get_exchange()

    changed = _track_existing(bot, exchange, state)
    changed = _scan_new(bot, exchange, state) or changed

    if changed:
        _save_state(bot, state)

    active = sum(
        1
        for item in state["positions"].values()
        if str(item.get("status", "")).upper() == "ACTIVE"
    )
    print("Smart Recovery DCA1 aktif takip:", active)
