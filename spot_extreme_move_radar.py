"""OKX USDT spot extreme-move radar.

Purpose:
- close the blind spot created by a futures-only trading universe,
- scan active OKX USDT spot markets every live cycle,
- detect early volume + price acceleration even when no OKX perpetual exists,
- send a clearly labelled Telegram heads-up without opening orders.

This is an opportunity radar, not a futures trade strategy. The existing Market
First futures engine remains unchanged. A coin with no active OKX perpetual (for
example an asset whose perpetual was delisted) can still be surfaced here.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from typing import Any, Dict, Iterable, Mapping, Optional

import ccxt

from telegram_delivery import send_telegram_once

VERSION = "SPOT_EXTREME_MOVE_RADAR_V1_2026_09_13"
STATE_FILE = "spot_extreme_move_state.json"
BOT_KEY = "SPOT_EXTREME_MOVE"

MIN_QUOTE_VOLUME_USDT = 75_000.0
PREFILTER_SAMPLE_MOVE_PERCENT = 1.20
PREFILTER_24H_MOVE_PERCENT = 8.0
MAX_DEEP_CANDIDATES = 24

MIN_SAMPLE_MOVE_PERCENT = 1.40
MIN_5M_MOVE_PERCENT = 1.80
MIN_15M_MOVE_PERCENT = 3.20
MIN_VOLUME_RATIO_5M = 1.60
MIN_1M_CONFIRM_PERCENT = 0.20

STRONG_5M_MOVE_PERCENT = 2.80
STRONG_15M_MOVE_PERCENT = 5.00
STRONG_VOLUME_RATIO_5M = 2.00

EXTREME_24H_PERCENT = 30.0
EXTREME_15M_PERCENT = 12.0
ALERT_COOLDOWN_SECONDS = 45 * 60
MAX_ALERT_HISTORY = 600

EXCLUDED_BASES = {
    "USDT", "USDC", "USDG", "DAI", "TUSD", "FDUSD", "USDE", "USDS",
    "EURT", "PYUSD", "RLUSD",
}


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


def _quote_volume(ticker: Mapping[str, Any]) -> float:
    direct = _sf(ticker.get("quoteVolume"))
    if direct > 0:
        return direct
    base = _sf(ticker.get("baseVolume"))
    last = _sf(ticker.get("last") or ticker.get("close"))
    return base * last if base > 0 and last > 0 else 0.0


def _change_24h(ticker: Mapping[str, Any]) -> float:
    direct = ticker.get("percentage")
    if direct is not None:
        return _sf(direct)
    last = _sf(ticker.get("last") or ticker.get("close"))
    open_price = _sf(ticker.get("open"))
    return _pct(open_price, last) if min(last, open_price) > 0 else 0.0


def _load_state(path: str = STATE_FILE) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        data = payload if isinstance(payload, dict) else {}
    except Exception:
        data = {}
    data.setdefault("version", VERSION)
    data.setdefault("previous_prices", {})
    data.setdefault("alerts", {})
    return data


def _save_state(state: Mapping[str, Any], path: str = STATE_FILE) -> None:
    payload = dict(state)
    payload["version"] = VERSION
    payload["updated_at"] = int(time.time())
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _active_usdt_spots(markets: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    perpetual_bases = {
        str(market.get("base") or "").upper()
        for market in markets.values()
        if market.get("active") is not False
        and bool(market.get("swap") or market.get("contract"))
        and market.get("linear") is not False
        and str(market.get("quote") or "").upper() == "USDT"
        and str(market.get("settle") or "USDT").upper() == "USDT"
        and market.get("expiry") in (None, 0, "")
    }

    for market in markets.values():
        if market.get("active") is False or not bool(market.get("spot")):
            continue
        if str(market.get("quote") or "").upper() != "USDT":
            continue
        base = str(market.get("base") or "").upper().strip()
        symbol = str(market.get("symbol") or "").strip()
        if not base or not symbol or base in EXCLUDED_BASES:
            continue
        label = f"{base}USDT"
        result[label] = {
            "label": label,
            "base": base,
            "ccxt_symbol": symbol,
            "perpetual_available": base in perpetual_bases,
        }
    return result


def _closed_ohlcv(candles: Iterable[Iterable[Any]], minimum: int = 8) -> list[list[float]]:
    rows: list[list[float]] = []
    for raw in candles or []:
        values = list(raw)
        if len(values) < 6:
            continue
        rows.append([_sf(value) for value in values[:6]])
    # Ignore the most recent candle because it can still be forming.
    return rows[:-1] if len(rows) >= minimum + 1 else []


def _candle_metrics(candles1m: Iterable[Iterable[Any]], candles5m: Iterable[Iterable[Any]]) -> Dict[str, float]:
    one = _closed_ohlcv(candles1m, minimum=10)
    five = _closed_ohlcv(candles5m, minimum=25)
    if len(one) < 10 or len(five) < 25:
        return {}

    move_1m = _pct(one[-2][4], one[-1][4])
    move_5m = _pct(five[-2][4], five[-1][4])
    move_15m = _pct(five[-4][4], five[-1][4])

    historical_volumes = [row[5] for row in five[-21:-1] if row[5] > 0]
    median_volume = statistics.median(historical_volumes) if historical_volumes else 0.0
    volume_ratio = five[-1][5] / median_volume if median_volume > 0 else 0.0

    return {
        "move_1m_percent": round(move_1m, 4),
        "move_5m_percent": round(move_5m, 4),
        "move_15m_percent": round(move_15m, 4),
        "volume_ratio_5m": round(volume_ratio, 3),
    }


def classify_setup(
    *,
    sample_move_percent: float,
    change_24h_percent: float,
    move_1m_percent: float,
    move_5m_percent: float,
    move_15m_percent: float,
    volume_ratio_5m: float,
) -> Optional[Dict[str, Any]]:
    """Return a strict PUMP/DUMP radar setup, or None.

    The first path is designed to catch ignition before a move becomes extreme.
    The second path catches a powerful continuation even if the prior sample was
    quiet. Signs must agree; a large 24h move alone is never enough.
    """
    directional_seed = sample_move_percent if abs(sample_move_percent) >= 0.15 else move_5m_percent
    if abs(directional_seed) < 0.15:
        return None
    sign = 1.0 if directional_seed > 0 else -1.0

    aligned_sample = sample_move_percent * sign
    aligned_1m = move_1m_percent * sign
    aligned_5m = move_5m_percent * sign
    aligned_15m = move_15m_percent * sign
    aligned_24h = change_24h_percent * sign

    if aligned_5m <= 0 or aligned_15m <= 0:
        return None
    if aligned_1m < -0.60:
        return None

    ignition = (
        aligned_sample >= MIN_SAMPLE_MOVE_PERCENT
        and aligned_5m >= MIN_5M_MOVE_PERCENT
        and aligned_15m >= MIN_15M_MOVE_PERCENT
        and volume_ratio_5m >= MIN_VOLUME_RATIO_5M
        and aligned_1m >= -0.10
    )
    strong_continuation = (
        aligned_5m >= STRONG_5M_MOVE_PERCENT
        and aligned_15m >= STRONG_15M_MOVE_PERCENT
        and volume_ratio_5m >= STRONG_VOLUME_RATIO_5M
        and aligned_1m >= MIN_1M_CONFIRM_PERCENT
    )
    daily_acceleration = (
        aligned_24h >= 12.0
        and aligned_5m >= 2.20
        and aligned_15m >= 4.0
        and volume_ratio_5m >= 1.80
        and aligned_1m >= 0.0
    )
    if not (ignition or strong_continuation or daily_acceleration):
        return None

    phase = "EXTREME" if (
        aligned_24h >= EXTREME_24H_PERCENT or aligned_15m >= EXTREME_15M_PERCENT
    ) else "EARLY"
    score = 0
    score += min(30, int(aligned_sample * 5))
    score += min(25, int(aligned_5m * 5))
    score += min(20, int(aligned_15m * 2))
    score += min(15, int(max(0.0, volume_ratio_5m - 1.0) * 6))
    score += min(10, int(max(0.0, aligned_24h) / 3.0))

    return {
        "direction": "PUMP" if sign > 0 else "DUMP",
        "phase": phase,
        "score": min(100, max(0, score)),
        "aligned_sample_percent": round(aligned_sample, 4),
        "aligned_1m_percent": round(aligned_1m, 4),
        "aligned_5m_percent": round(aligned_5m, 4),
        "aligned_15m_percent": round(aligned_15m, 4),
        "aligned_24h_percent": round(aligned_24h, 4),
        "volume_ratio_5m": round(volume_ratio_5m, 3),
    }


def _alert_key(label: str, direction: str) -> str:
    return f"{label}:{direction}"


def should_alert(state: Mapping[str, Any], label: str, setup: Mapping[str, Any], now: int) -> bool:
    alerts = state.get("alerts") if isinstance(state.get("alerts"), Mapping) else {}
    previous = alerts.get(_alert_key(label, str(setup.get("direction"))))
    if not isinstance(previous, Mapping):
        return True
    last_at = int(_sf(previous.get("at")))
    old_phase = str(previous.get("phase") or "")
    new_phase = str(setup.get("phase") or "")
    if old_phase != "EXTREME" and new_phase == "EXTREME":
        return True
    return not last_at or now - last_at >= ALERT_COOLDOWN_SECONDS


def _record_alert(state: Dict[str, Any], label: str, setup: Mapping[str, Any], price: float, now: int) -> None:
    alerts = state.setdefault("alerts", {})
    alerts[_alert_key(label, str(setup.get("direction")))] = {
        "at": int(now),
        "phase": setup.get("phase"),
        "score": int(_sf(setup.get("score"))),
        "price": round(price, 12),
    }
    if len(alerts) > MAX_ALERT_HISTORY:
        ordered = sorted(
            alerts.items(),
            key=lambda item: int(_sf((item[1] or {}).get("at"))),
            reverse=True,
        )
        state["alerts"] = dict(ordered[:MAX_ALERT_HISTORY])


def format_message(row: Mapping[str, Any], setup: Mapping[str, Any]) -> str:
    direction = str(setup.get("direction"))
    phase = str(setup.get("phase"))
    icon = "🚀" if direction == "PUMP" else "📉"
    phase_text = "ERKEN İVME" if phase == "EARLY" else "AŞIRI HAREKET / KOVALAMA RİSKİ"
    perpetual = "VAR" if bool(row.get("perpetual_available")) else "YOK"
    action_note = (
        "OKX perpetual yok; bu uyarı spot fırsat/risk takibidir."
        if perpetual == "YOK"
        else "OKX perpetual var; ana futures sisteminden bağımsız momentum alarmıdır."
    )
    return (
        f"{icon} SPOT EXTREME RADAR | {row.get('label')}\n\n"
        f"📊 Yön: {direction} | {phase_text}\n"
        f"⭐ Radar skoru: {int(_sf(setup.get('score')))}/100\n"
        f"💵 Fiyat: {_sf(row.get('price')):.10g}\n"
        f"⚡ Son tarama: {_sf(row.get('sample_move_percent')):+.2f}%\n"
        f"⏱ 1M: {_sf(row.get('move_1m_percent')):+.2f}% | 5M: {_sf(row.get('move_5m_percent')):+.2f}% | 15M: {_sf(row.get('move_15m_percent')):+.2f}%\n"
        f"📅 24S: {_sf(row.get('change_24h_percent')):+.2f}%\n"
        f"🔊 5M hacim: {_sf(row.get('volume_ratio_5m')):.2f}x\n"
        f"💧 24S hacim: ${_sf(row.get('quote_volume')):,.0f}\n"
        f"🏷 OKX spot: VAR | OKX perpetual: {perpetual}\n"
        f"ℹ️ {action_note}\n"
        "⚠️ Otomatik emir açılmaz; EXTREME etiketi geç giriş riskinin yüksek olduğunu gösterir."
    )


def _send(message: str) -> bool:
    token = str(os.getenv("TOKEN") or "").strip()
    chat_id = str(os.getenv("CHAT_ID") or "").strip()
    live = str(os.getenv("GITHUB_REF_NAME") or "").strip() == "main"
    if not live or not token or not chat_id:
        print("SPOT RADAR TEST/NO TOKEN |", message.replace("\n", " | "))
        return True
    return send_telegram_once(
        message=message,
        telegram_token=token,
        chat_id=chat_id,
        bot_key=BOT_KEY,
        delivery_key=None,
    )


def _exchange() -> Any:
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })


def run() -> Dict[str, Any]:
    exchange = _exchange()
    state = _load_state()
    now = int(time.time())

    markets = exchange.load_markets()
    spots = _active_usdt_spots(markets)
    tickers = exchange.fetch_tickers()
    previous_prices = state.get("previous_prices") if isinstance(state.get("previous_prices"), Mapping) else {}

    rows: list[Dict[str, Any]] = []
    for label, meta in spots.items():
        ticker = tickers.get(str(meta.get("ccxt_symbol"))) or {}
        price = _sf(ticker.get("last") or ticker.get("close"))
        if price <= 0:
            continue
        quote_volume = _quote_volume(ticker)
        change24 = _change_24h(ticker)
        previous = _sf(previous_prices.get(label))
        sample_move = _pct(previous, price) if previous > 0 else 0.0
        if quote_volume < MIN_QUOTE_VOLUME_USDT:
            continue
        if (
            previous > 0
            and abs(sample_move) < PREFILTER_SAMPLE_MOVE_PERCENT
            and abs(change24) < PREFILTER_24H_MOVE_PERCENT
        ):
            continue
        if previous <= 0 and abs(change24) < PREFILTER_24H_MOVE_PERCENT:
            continue
        rows.append({
            **meta,
            "price": price,
            "quote_volume": quote_volume,
            "change_24h_percent": change24,
            "sample_move_percent": sample_move,
        })

    rows.sort(
        key=lambda row: (
            1 if not bool(row.get("perpetual_available")) else 0,
            abs(_sf(row.get("sample_move_percent"))) * 4.0 + abs(_sf(row.get("change_24h_percent"))),
            _sf(row.get("quote_volume")),
        ),
        reverse=True,
    )

    alerts_sent = 0
    evaluated = 0
    candidates: list[Dict[str, Any]] = []
    for row in rows[:MAX_DEEP_CANDIDATES]:
        try:
            symbol = str(row.get("ccxt_symbol"))
            candles1m = exchange.fetch_ohlcv(symbol, "1m", limit=50)
            candles5m = exchange.fetch_ohlcv(symbol, "5m", limit=50)
            metrics = _candle_metrics(candles1m, candles5m)
            if not metrics:
                continue
            row.update(metrics)
            evaluated += 1
            setup = classify_setup(
                sample_move_percent=_sf(row.get("sample_move_percent")),
                change_24h_percent=_sf(row.get("change_24h_percent")),
                move_1m_percent=_sf(row.get("move_1m_percent")),
                move_5m_percent=_sf(row.get("move_5m_percent")),
                move_15m_percent=_sf(row.get("move_15m_percent")),
                volume_ratio_5m=_sf(row.get("volume_ratio_5m")),
            )
            if not setup:
                continue
            row["setup"] = setup
            candidates.append({
                "symbol": row.get("label"),
                "perpetual_available": bool(row.get("perpetual_available")),
                "price": row.get("price"),
                "sample_move_percent": row.get("sample_move_percent"),
                "change_24h_percent": row.get("change_24h_percent"),
                **setup,
            })
            if should_alert(state, str(row.get("label")), setup, now):
                if _send(format_message(row, setup)):
                    _record_alert(state, str(row.get("label")), setup, _sf(row.get("price")), now)
                    alerts_sent += 1
        except Exception as exc:
            print("SPOT RADAR aday hatası:", row.get("label"), type(exc).__name__, exc)

    # Always refresh the complete spot price snapshot. A 5-minute sample move is
    # only meaningful if quiet coins are remembered before they start moving.
    refreshed_prices: Dict[str, float] = {}
    for label, meta in spots.items():
        ticker = tickers.get(str(meta.get("ccxt_symbol"))) or {}
        price = _sf(ticker.get("last") or ticker.get("close"))
        if price > 0:
            refreshed_prices[label] = price
    state["previous_prices"] = refreshed_prices
    state["last_run"] = {
        "at": now,
        "spot_markets": len(spots),
        "prefilter_candidates": len(rows),
        "deep_evaluated": evaluated,
        "qualified": len(candidates),
        "alerts_sent": alerts_sent,
        "top_candidates": candidates[:20],
    }
    _save_state(state)

    summary = {
        "version": VERSION,
        "spot_markets": len(spots),
        "prefilter_candidates": len(rows),
        "deep_evaluated": evaluated,
        "qualified": len(candidates),
        "alerts_sent": alerts_sent,
    }
    print("SPOT EXTREME RADAR:", summary)
    return summary


if __name__ == "__main__":
    run()
