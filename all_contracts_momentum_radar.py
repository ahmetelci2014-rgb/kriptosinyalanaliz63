"""All Contracts Momentum Radar.

Purpose:
- Watch every active linear USDT perpetual contract exposed by Binance USD-M
  and OKX, including TradFi/RWA perps when the exchange lists them.
- Detect fresh acceleration before the main strategy's 1H/15M/5M entry gates.
- Send Telegram *awareness* alerts only. No orders, no trade ledger mutation.

The main crypto strategy remains unchanged. This is a separate market-motion
watcher designed to answer: "Where is unusual movement starting right now?"
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import ccxt
import requests

VERSION = "ALL_CONTRACTS_MOMENTUM_RADAR_V1_2026_08_28"
STATE_FILE = os.getenv("MOMENTUM_STATE_FILE", "all_contracts_momentum_state.json")

POLL_SECONDS = max(10, int(os.getenv("MOMENTUM_POLL_SECONDS", "20")))
WATCH_SECONDS = max(30, int(os.getenv("MOMENTUM_WATCH_SECONDS", "250")))
DEEP_SCAN_LIMIT_PER_POLL = max(8, int(os.getenv("MOMENTUM_DEEP_SCAN_LIMIT", "24")))

# Discovery deliberately has no top-N universe cap and no 24h minimum-volume
# gate. Liquidity is reported as context instead of being used to hide movers.
SAMPLE_MOVE_TRIGGER_PERCENT = float(os.getenv("MOMENTUM_SAMPLE_TRIGGER_PCT", "0.35"))
TICKER_24H_TRIGGER_PERCENT = float(os.getenv("MOMENTUM_24H_TRIGGER_PCT", "4.0"))
MIN_1M_MOVE_PERCENT = float(os.getenv("MOMENTUM_MIN_1M_PCT", "0.65"))
MIN_3M_MOVE_PERCENT = float(os.getenv("MOMENTUM_MIN_3M_PCT", "0.90"))
MIN_5M_MOVE_PERCENT = float(os.getenv("MOMENTUM_MIN_5M_PCT", "1.20"))
MIN_1M_RANGE_PERCENT = float(os.getenv("MOMENTUM_MIN_RANGE_PCT", "0.85"))
MIN_VOLUME_RATIO = float(os.getenv("MOMENTUM_MIN_VOLUME_RATIO", "2.0"))
MIN_ALERT_SCORE = int(os.getenv("MOMENTUM_MIN_ALERT_SCORE", "5"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("MOMENTUM_COOLDOWN_SECONDS", "900"))

MAX_STATE_ALERTS = 1200
HTTP_TIMEOUT_SECONDS = 12


@dataclass(frozen=True)
class Contract:
    exchange_id: str
    normalized: str
    ccxt_symbol: str
    label: str


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
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _telegram_send(text: str) -> bool:
    token = str(os.getenv("TOKEN") or "").strip()
    chat_id = str(os.getenv("CHAT_ID") or "").strip()
    if not token or not chat_id:
        print("Telegram secrets yok; mesaj konsola yazildi:\n", text)
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if response.ok:
            return True
        print("Telegram hata:", response.status_code, response.text[:300])
    except Exception as exc:
        print("Telegram exception:", exc)
    return False


def _build_exchange(exchange_id: str) -> Any:
    if exchange_id == "binanceusdm":
        return ccxt.binanceusdm({
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
    if exchange_id == "okx":
        return ccxt.okx({
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
    raise ValueError(exchange_id)


def _is_linear_usdt_perp(market: Dict[str, Any]) -> bool:
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
    # We only want perpetual-style contracts, not dated futures.
    expiry = market.get("expiry")
    if expiry not in (None, 0, ""):
        return False
    return True


def _contract_label(market: Dict[str, Any]) -> str:
    base = str(market.get("base") or "").upper().strip()
    if base:
        return f"{base}USDT"
    return str(market.get("symbol") or "").upper().replace("/", "").replace(":", "")


def load_contracts(exchange: Any, exchange_id: str) -> Dict[str, Contract]:
    markets = exchange.load_markets(reload=True)
    rows: Dict[str, Contract] = {}
    for _, market in markets.items():
        if not _is_linear_usdt_perp(market):
            continue
        ccxt_symbol = str(market.get("symbol") or "").strip()
        if not ccxt_symbol:
            continue
        label = _contract_label(market)
        normalized = f"{exchange_id}:{label}"
        rows[ccxt_symbol] = Contract(
            exchange_id=exchange_id,
            normalized=normalized,
            ccxt_symbol=ccxt_symbol,
            label=label,
        )
    return rows


def _ticker_last(ticker: Dict[str, Any]) -> float:
    return _sf(ticker.get("last") or ticker.get("close"))


def _ticker_quote_volume(ticker: Dict[str, Any]) -> float:
    direct = _sf(ticker.get("quoteVolume"))
    if direct > 0:
        return direct
    base = _sf(ticker.get("baseVolume"))
    last = _ticker_last(ticker)
    return base * last if base > 0 and last > 0 else 0.0


def _ticker_24h_percent(ticker: Dict[str, Any]) -> float:
    pct = _sf(ticker.get("percentage"))
    if pct:
        return pct
    last = _ticker_last(ticker)
    open_price = _sf(ticker.get("open"))
    return _pct(open_price, last) if min(last, open_price) > 0 else 0.0


def _fetch_all_tickers(exchange: Any) -> Dict[str, Dict[str, Any]]:
    try:
        return exchange.fetch_tickers()
    except Exception as exc:
        print(exchange.id, "fetch_tickers hata:", exc)
        return {}


def _ohlcv_rows(raw: Iterable[Iterable[Any]]) -> List[List[float]]:
    rows: List[List[float]] = []
    for candle in raw or []:
        values = list(candle)
        if len(values) < 6:
            continue
        rows.append([
            _sf(values[0]), _sf(values[1]), _sf(values[2]),
            _sf(values[3]), _sf(values[4]), _sf(values[5]),
        ])
    return rows


def analyze_1m(exchange: Any, contract: Contract, current_price: float) -> Optional[Dict[str, Any]]:
    try:
        rows = _ohlcv_rows(exchange.fetch_ohlcv(contract.ccxt_symbol, "1m", limit=32))
    except Exception as exc:
        print(contract.normalized, "1m veri hata:", exc)
        return None
    if len(rows) < 24:
        return None

    # Intentionally INCLUDE the forming 1m candle. The old all-market prefilter
    # discarded forming candles, which is exactly where fresh acceleration lives.
    current = rows[-1]
    prev = rows[-2]
    last3 = rows[-3:]
    last5 = rows[-5:]
    baseline = rows[-24:-4]
    prior20 = rows[-24:-4]

    open_1m = current[1]
    high_1m = current[2]
    low_1m = current[3]
    close_1m = current_price if current_price > 0 else current[4]
    open_3m = last3[0][1]
    open_5m = last5[0][1]

    if min(open_1m, open_3m, open_5m, close_1m) <= 0:
        return None

    move_1m = _pct(open_1m, close_1m)
    move_3m = _pct(open_3m, close_1m)
    move_5m = _pct(open_5m, close_1m)
    range_1m = (high_1m - low_1m) / open_1m * 100.0 if open_1m > 0 else 0.0

    baseline_volumes = [row[5] for row in baseline if row[5] > 0]
    median_volume = statistics.median(baseline_volumes) if baseline_volumes else 0.0
    volume_ratio = current[5] / median_volume if median_volume > 0 else 0.0

    prior_high = max(row[2] for row in prior20)
    prior_low = min(row[3] for row in prior20)
    breakout_up = close_1m > prior_high > 0
    breakout_down = close_1m < prior_low and prior_low > 0

    direction = "LONG" if move_3m >= 0 else "SHORT"
    directional_moves = [
        abs(move_1m) >= MIN_1M_MOVE_PERCENT,
        abs(move_3m) >= MIN_3M_MOVE_PERCENT,
        abs(move_5m) >= MIN_5M_MOVE_PERCENT,
    ]
    breakout = breakout_up if direction == "LONG" else breakout_down
    same_direction = (
        (move_1m > 0 and move_3m > 0 and move_5m > 0)
        or (move_1m < 0 and move_3m < 0 and move_5m < 0)
    )

    score = 0
    score += 2 if directional_moves[0] else 0
    score += 2 if directional_moves[1] else 0
    score += 2 if directional_moves[2] else 0
    score += 2 if volume_ratio >= MIN_VOLUME_RATIO else 0
    score += 1 if range_1m >= MIN_1M_RANGE_PERCENT else 0
    score += 2 if breakout else 0
    score += 1 if same_direction else 0
    score += 1 if abs(_pct(prev[1], prev[4])) >= 0.35 and (prev[4] - prev[1]) * move_3m > 0 else 0

    qualifies = score >= MIN_ALERT_SCORE and (
        any(directional_moves)
        or (breakout and volume_ratio >= MIN_VOLUME_RATIO)
    )

    return {
        "qualifies": qualifies,
        "direction": direction,
        "score": score,
        "move_1m_percent": round(move_1m, 3),
        "move_3m_percent": round(move_3m, 3),
        "move_5m_percent": round(move_5m, 3),
        "range_1m_percent": round(range_1m, 3),
        "volume_ratio": round(volume_ratio, 2),
        "breakout": bool(breakout),
        "same_direction": bool(same_direction),
    }


def _cooldown_key(contract: Contract, direction: str) -> str:
    return f"{contract.normalized}:{direction}"


def _can_alert(state: Dict[str, Any], contract: Contract, direction: str, now: int) -> bool:
    alerts = state.setdefault("alerts", {})
    last = int((alerts.get(_cooldown_key(contract, direction)) or {}).get("at") or 0)
    return now - last >= ALERT_COOLDOWN_SECONDS


def _mark_alert(
    state: Dict[str, Any],
    contract: Contract,
    direction: str,
    now: int,
    detail: Dict[str, Any],
) -> None:
    alerts = state.setdefault("alerts", {})
    alerts[_cooldown_key(contract, direction)] = {
        "at": now,
        "score": detail.get("score"),
        "move_5m_percent": detail.get("move_5m_percent"),
    }
    if len(alerts) > MAX_STATE_ALERTS:
        ordered = sorted(
            alerts.items(),
            key=lambda item: int((item[1] or {}).get("at") or 0),
            reverse=True,
        )
        state["alerts"] = dict(ordered[:MAX_STATE_ALERTS])


def _format_volume(quote_volume: float) -> str:
    if quote_volume >= 1_000_000_000:
        return f"{quote_volume / 1_000_000_000:.2f}B"
    if quote_volume >= 1_000_000:
        return f"{quote_volume / 1_000_000:.2f}M"
    if quote_volume >= 1_000:
        return f"{quote_volume / 1_000:.1f}K"
    return f"{quote_volume:.0f}"


def build_message(
    contract: Contract,
    detail: Dict[str, Any],
    quote_volume: float,
    change_24h: float,
) -> str:
    direction = detail["direction"]
    icon = "🟢" if direction == "LONG" else "🔴"
    exchange_name = "BINANCE FUTURES" if contract.exchange_id == "binanceusdm" else "OKX SWAP"
    strength = "🔥 GÜÇLÜ" if int(detail.get("score") or 0) >= 8 else "⚡ ERKEN"
    breakout = "EVET" if detail.get("breakout") else "hayır"
    return (
        f"🚨 ANİ İVME RADARI — {strength}\n"
        f"{icon} {direction} | {contract.label}\n"
        f"🏦 {exchange_name}\n"
        f"⚡ 1M: {detail['move_1m_percent']:+.2f}% | "
        f"3M: {detail['move_3m_percent']:+.2f}% | "
        f"5M: {detail['move_5m_percent']:+.2f}%\n"
        f"🔊 1M hacim anomalisi: {detail['volume_ratio']:.2f}x\n"
        f"💥 20M tepe/dip kırılımı: {breakout}\n"
        f"📏 1M range: {detail['range_1m_percent']:.2f}% | skor {detail['score']}/13\n"
        f"🌊 24s değişim: {change_24h:+.2f}% | hacim ≈ {_format_volume(quote_volume)} USDT\n"
        f"👀 Amaç: hareketi erken fark etmek; Premium işlem teyidi ayrıca değerlendirilir.\n"
        f"⚠️ Bu mesaj otomatik emir veya kesin al/sat tavsiyesi değildir."
    )


def run() -> None:
    enabled_ids: List[str] = []
    if str(os.getenv("MOMENTUM_BINANCE", "1")).lower() not in {"0", "false", "no"}:
        enabled_ids.append("binanceusdm")
    if str(os.getenv("MOMENTUM_OKX", "1")).lower() not in {"0", "false", "no"}:
        enabled_ids.append("okx")

    state = _load_state()
    state["version"] = VERSION
    state.setdefault("alerts", {})
    state.setdefault("stats", {})

    contexts: Dict[str, Tuple[Any, Dict[str, Contract]]] = {}
    for exchange_id in enabled_ids:
        try:
            exchange = _build_exchange(exchange_id)
            contracts = load_contracts(exchange, exchange_id)
            contexts[exchange_id] = (exchange, contracts)
            print(exchange_id, "aktif USDT perpetual:", len(contracts))
        except Exception as exc:
            print(exchange_id, "market yukleme hata:", exc)

    if not contexts:
        raise RuntimeError("Momentum radar icin kullanilabilir borsa yok.")

    previous_prices: Dict[str, float] = {}
    started = time.time()
    polls = 0
    deep_scans = 0
    alerts_sent = 0

    while True:
        polls += 1
        poll_started = time.time()
        now = int(time.time())

        for exchange_id, (exchange, contracts) in contexts.items():
            tickers = _fetch_all_tickers(exchange)
            pre_candidates: List[Tuple[float, Contract, Dict[str, Any], float, float, float]] = []

            for ccxt_symbol, contract in contracts.items():
                ticker = tickers.get(ccxt_symbol) or {}
                last = _ticker_last(ticker)
                if last <= 0:
                    continue
                quote_volume = _ticker_quote_volume(ticker)
                change_24h = _ticker_24h_percent(ticker)
                prev = previous_prices.get(contract.normalized)
                sample_move = _pct(prev, last) if prev and prev > 0 else 0.0
                previous_prices[contract.normalized] = last

                # First poll has no sample delta. Still allow very active daily movers
                # into a deep scan so a workflow start cannot completely blind us.
                should_deep_scan = (
                    abs(sample_move) >= SAMPLE_MOVE_TRIGGER_PERCENT
                    or abs(change_24h) >= TICKER_24H_TRIGGER_PERCENT
                )
                if not should_deep_scan:
                    continue

                priority = abs(sample_move) * 3.0 + min(abs(change_24h), 20.0) * 0.15
                pre_candidates.append(
                    (priority, contract, ticker, last, quote_volume, change_24h)
                )

            pre_candidates.sort(key=lambda row: row[0], reverse=True)

            for _, contract, ticker, last, quote_volume, change_24h in pre_candidates[:DEEP_SCAN_LIMIT_PER_POLL]:
                detail = analyze_1m(exchange, contract, last)
                deep_scans += 1
                if not detail or not detail.get("qualifies"):
                    continue
                direction = str(detail["direction"])
                if not _can_alert(state, contract, direction, now):
                    continue
                message = build_message(contract, detail, quote_volume, change_24h)
                if _telegram_send(message):
                    alerts_sent += 1
                _mark_alert(state, contract, direction, now, detail)
                print(
                    "ALERT", contract.normalized, direction,
                    "score=", detail.get("score"),
                    "5m=", detail.get("move_5m_percent"),
                )

        elapsed = time.time() - started
        if elapsed >= WATCH_SECONDS:
            break
        sleep_for = max(0.0, POLL_SECONDS - (time.time() - poll_started))
        time.sleep(sleep_for)

    state["updated_at"] = int(time.time())
    state["stats"] = {
        "polls": polls,
        "deep_scans": deep_scans,
        "alerts_sent": alerts_sent,
        "watch_seconds": WATCH_SECONDS,
        "poll_seconds": POLL_SECONDS,
        "exchanges": {
            exchange_id: len(contracts)
            for exchange_id, (_, contracts) in contexts.items()
        },
    }
    _save_state(state)
    print("MOMENTUM RADAR tamamlandi:", state["stats"])


if __name__ == "__main__":
    run()
