#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tüm Piyasa Keşif ve Sanal Performans Radarı V1

AMAÇ
----
Premium ana botun canlı TOP300 evrenine DOKUNMADAN, OKX'teki bütün uygun
USDT perpetual swap piyasalarını görünür hale getirir.

Her çalışmada:
1) Bütün uygun aktif USDT swap marketleri + ticker'ları okunur.
2) Mevcut Premium TOP300 evreni, config.py'deki kurallarla yeniden üretilir.
3) TOP300 dışındaki coinler OUTSIDE300 olarak ayrılır.
4) OUTSIDE300 coinler dönerli (rotation) + sıcak aday öncelikli derin MTF taranır.
5) Mevcut strategy.py içindeki analyze_mtf_trade / analyze_5m_radar kullanılır.
6) Gerçek TRADE kalitesine çıkan adaylar SANAL işlem olarak kaydedilir.
7) TP1/TP2/TP3/SL/BE/EXPIRED ve exact R sessizce takip edilir.

GÜVENLİK
--------
- Telegram göndermez.
- OKX emri açmaz.
- main.py / strategy.py / config.py değiştirmez.
- Premium açık işlem limitini veya risk modunu etkilemez.
- Ayrı state/ledger kullanır.
- Canlı sinyal değildir; araştırma ve performans doğrulamasıdır.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "ALL_MARKET_SHADOW_V1_2_2026_08_11"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_FILTER_CHANGE"

STATE_FILE = Path("all_market_shadow_state.json")
LEDGER_FILE = Path("all_market_shadow_ledger.json")

TR_TZ = timezone(timedelta(hours=3))

# Her turda derin MTF analizine sokulacak OUTSIDE300 coin üst sınırı.
# Bütün uygun marketlerin ticker'ı her tur görülür; ağır OHLCV taraması rotation ile yapılır.
MAX_DEEP_SCAN_PER_RUN = 60
HOT_CANDIDATES_PER_RUN = 15

# Sanal portföy; gerçek risk limiti değildir. Veri toplamayı sınırlamak için kullanılır.
MAX_OPEN_SHADOW_TRADES = 30
MAX_OPEN_HOURS = 18

# Premium davranışına yakın tekrar koruması.
DUPLICATE_SECONDS = 90 * 60
RECENT_CLOSED_COOLDOWN_SECONDS = 4 * 60 * 60
STOP_COOLDOWN_SECONDS = 6 * 60 * 60

# Takipte her tur en fazla son 180 adet 1M mum istenir.
# Workflow 15 dakikada bir çalıştığı için normal koşulda fazlasıyla yeterlidir.
TRACK_TIMEFRAME = "1m"
TRACK_LIMIT = 180

STABLE_BASES = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDP", "USD",
}


# ---------------------------------------------------------------------
# Genel yardımcılar
# ---------------------------------------------------------------------

def now_ts() -> int:
    return int(time.time())


def tr_text(ts: Optional[int] = None) -> str:
    value = int(ts if ts is not None else now_ts())
    return datetime.fromtimestamp(value, tz=TR_TZ).strftime("%Y-%m-%d %H:%M:%S")


def day_key(ts: Optional[int] = None) -> str:
    value = int(ts if ts is not None else now_ts())
    return datetime.fromtimestamp(value, tz=TR_TZ).strftime("%Y-%m-%d")


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    number = safe_float(value, None)
    return int(number) if number is not None else default


def pct(n: Any, d: Any) -> Optional[float]:
    n = safe_float(n, None)
    d = safe_float(d, None)
    if n is None or d is None or d <= 0:
        return None
    return round(n / d * 100.0, 2)


def load_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = default if isinstance(default, dict) else {}
    if not path.exists():
        return dict(fallback)
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return dict(fallback)
        data = json.loads(text)
        return data if isinstance(data, dict) else dict(fallback)
    except Exception as exc:
        print(path, "okuma hatası:", exc)
        return dict(fallback)


def save_json_atomic(path: Path, data: Dict[str, Any]) -> bool:
    temp_name: Optional[str] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(temp_name, "r", encoding="utf-8") as verify:
            checked = json.load(verify)
        if not isinstance(checked, dict):
            raise ValueError("JSON kökü object değil.")

        os.replace(temp_name, path)
        temp_name = None
        return True
    except Exception as exc:
        print(path, "atomik kaydetme hatası:", exc)
        return False
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass


def bot_symbol(okx_symbol: str) -> str:
    base = str(okx_symbol or "").split("/")[0]
    return f"{base}USDT".upper()


def okx_symbol(symbol: str) -> str:
    base = str(symbol or "").upper()
    if base.endswith("USDT"):
        base = base[:-4]
    return f"{base}/USDT:USDT"


def legacy_premium_volume(ticker: Dict[str, Any]) -> float:
    """
    Mevcut canlı Premium main.py davranışını AYNEN referanslamak içindir.
    Bu değer canlı tarama evrenini taklit eder; doğru USDT notional olduğu
    varsayılmaz.
    """
    value = safe_float(ticker.get("quoteVolume"), None)
    if value is not None:
        return max(0.0, value)

    info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}
    for key in ("volCcy24h", "volUsd24h", "vol24h"):
        value = safe_float(info.get(key), None)
        if value is not None:
            return max(0.0, value)
    return 0.0


def corrected_quote_notional_24h(ticker: Dict[str, Any]) -> float:
    """
    OKX USDT SWAP için yaklaşık 24s USDT notional.

    OKX dokümanında derivatives volCcy24h = base currency miktarıdır.
    Bu yüzden USDT-margined linear swap için:
        yaklaşık USDT notional = volCcy24h * last

    Bu yalnız EVREN/AUDIT ölçümüdür; gerçek PnL hesabında kullanılmaz.
    """
    info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}

    last = safe_float(ticker.get("last"), None)
    if last is None:
        last = safe_float(info.get("last"), None)

    base_amount = safe_float(info.get("volCcy24h"), None)
    if base_amount is not None and last is not None and last > 0:
        return max(0.0, base_amount * last)

    # Raw OKX alanı yoksa unified baseVolume ile yaklaşıkla.
    base_volume = safe_float(ticker.get("baseVolume"), None)
    if base_volume is not None and last is not None and last > 0:
        return max(0.0, base_volume * last)

    # Son çare: gerçekten quoteVolume sağlayan bir adapter varsa kullan.
    quote_volume = safe_float(ticker.get("quoteVolume"), None)
    if quote_volume is not None:
        return max(0.0, quote_volume)

    return 0.0


def ticker_change_percent(ticker: Dict[str, Any]) -> float:
    for key in ("percentage", "change"):
        value = safe_float(ticker.get(key), None)
        if value is not None:
            if key == "percentage":
                return value

    open_price = safe_float(ticker.get("open"), None)
    last = safe_float(ticker.get("last"), None)
    if open_price and last and open_price > 0:
        return (last - open_price) / open_price * 100.0
    return 0.0


# ---------------------------------------------------------------------
# Evren: Premium TOP300 ve OUTSIDE300
# ---------------------------------------------------------------------

def eligible_markets(markets: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        if not market.get("active", True):
            continue
        if not market.get("swap", False):
            continue
        if str(market.get("quote") or "").upper() != "USDT":
            continue
        if str(market.get("settle") or "").upper() != "USDT":
            continue

        symbol = str(market.get("symbol") or "")
        if "/USDT:USDT" not in symbol:
            continue

        base = str(market.get("base") or "").upper()
        if not base or base in STABLE_BASES:
            continue

        rows.append({
            "okx_symbol": symbol,
            "symbol": bot_symbol(symbol),
            "base": base,
        })

    # Aynı bot sembolüne map olan tekrarları tekilleştir.
    dedup: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        dedup[row["symbol"]] = row
    return sorted(dedup.values(), key=lambda x: x["symbol"])


def build_universe(
    markets: Dict[str, Dict[str, Any]],
    tickers: Dict[str, Dict[str, Any]],
    priority_coins: Sequence[str],
    min_quote_volume: float,
    max_scan_coins: int,
) -> Dict[str, Any]:
    """
    İki paralel evren kurar:

    1) live_reference_symbols:
       Canlı Premium V7 davranışını taklit eder: priority coinler aktifse hacim filtresinden düşmez.
       OUTSIDE300 sanal işlemler bu canlı referansa göre seçilir.

    2) corrected_symbols:
       OKX derivatives volCcy24h * last ile yaklaşık USDT notional kullanır.
       Bu evren canlıya uygulanmaz; yalnız yanlış dışlama auditidir.
    """
    eligible = eligible_markets(markets)

    enriched = []
    for row in eligible:
        ticker = tickers.get(row["okx_symbol"], {})
        ticker = ticker if isinstance(ticker, dict) else {}

        legacy_volume = legacy_premium_volume(ticker)
        corrected_notional = corrected_quote_notional_24h(ticker)
        change = ticker_change_percent(ticker)
        last = safe_float(ticker.get("last"), None)

        enriched.append({
            **row,
            # Geriye dönük alan adı canlı/legacy hacmi göstermeye devam eder.
            "quote_volume_24h": round(legacy_volume, 4),
            "legacy_premium_volume_24h": round(legacy_volume, 4),
            "corrected_quote_notional_24h": round(corrected_notional, 4),
            "change_24h_percent": round(change, 4),
            "last": last,
        })

    # -------- Mevcut canlı Premium referansı --------
    legacy_rows = [
        row for row in enriched
        if row["legacy_premium_volume_24h"] >= float(min_quote_volume)
    ]
    legacy_rows.sort(
        key=lambda row: row["legacy_premium_volume_24h"],
        reverse=True,
    )

    legacy_symbols = [row["symbol"] for row in legacy_rows]

    # Canlı Premium V7 ile aynı davranış:
    # aktif/uygun PRIORITY_COINS minimum hacim filtresinden bağımsız korunur.
    eligible_symbol_set = {
        row["symbol"]
        for row in enriched
    }
    priority = [
        str(symbol).upper()
        for symbol in priority_coins
        if str(symbol).upper() in eligible_symbol_set
    ]
    priority_set = set(priority)

    forced_priority_symbols = [
        row["symbol"]
        for row in enriched
        if (
            row["symbol"] in priority_set
            and row["legacy_premium_volume_24h"]
            < float(min_quote_volume)
        )
    ]

    legacy_others = [
        symbol for symbol in legacy_symbols
        if symbol not in priority_set
    ]
    live_reference_symbols = (
        priority + legacy_others
    )[: int(max_scan_coins)]
    live_reference_set = set(live_reference_symbols)

    legacy_rank = {
        row["symbol"]: index + 1
        for index, row in enumerate(
            sorted(
                enriched,
                key=lambda x: x["legacy_premium_volume_24h"],
                reverse=True,
            )
        )
    }

    # -------- Düzeltilmiş yaklaşık USDT notional evren --------
    corrected_rows = [
        row for row in enriched
        if row["corrected_quote_notional_24h"] >= float(min_quote_volume)
    ]
    corrected_rows.sort(
        key=lambda row: row["corrected_quote_notional_24h"],
        reverse=True,
    )

    corrected_symbols_all = [row["symbol"] for row in corrected_rows]
    corrected_priority = [
        str(symbol).upper()
        for symbol in priority_coins
        if str(symbol).upper() in corrected_symbols_all
    ]
    corrected_others = [
        symbol for symbol in corrected_symbols_all
        if symbol not in corrected_priority
    ]
    corrected_symbols = (
        corrected_priority + corrected_others
    )[: int(max_scan_coins)]
    corrected_set = set(corrected_symbols)

    corrected_rank = {
        row["symbol"]: index + 1
        for index, row in enumerate(
            sorted(
                enriched,
                key=lambda x: x["corrected_quote_notional_24h"],
                reverse=True,
            )
        )
    }

    # -------- Canlı Premium dışında kalan gerçek araştırma evreni --------
    outside = []
    for row in enriched:
        symbol = row["symbol"]
        if symbol in live_reference_set:
            continue

        legacy_above_min = (
            row["legacy_premium_volume_24h"] >= float(min_quote_volume)
        )
        corrected_above_min = (
            row["corrected_quote_notional_24h"] >= float(min_quote_volume)
        )
        corrected_in_top = symbol in corrected_set

        if not legacy_above_min:
            live_outside_reason = "BELOW_PREMIUM_MIN_VOLUME_LEGACY"
        else:
            live_outside_reason = "OUTSIDE_PREMIUM_TOP300_LEGACY"

        if corrected_in_top:
            audit_class = "LIVE_OUTSIDE_BUT_CORRECTED_TOP300"
        elif corrected_above_min:
            audit_class = "LIVE_OUTSIDE_CORRECTED_ABOVE_MIN_NOT_TOP300"
        else:
            audit_class = "BELOW_CORRECTED_MIN_VOLUME"

        outside.append({
            **row,
            "volume_rank_all_eligible": legacy_rank.get(symbol),
            "legacy_volume_rank_all_eligible": legacy_rank.get(symbol),
            "corrected_volume_rank_all_eligible": corrected_rank.get(symbol),
            "outside_reason": live_outside_reason,
            "volume_audit_class": audit_class,
            "corrected_above_min_volume": corrected_above_min,
            "corrected_in_top300": corrected_in_top,
        })

    # Önce canlı dışında olup düzeltilmiş evrende TOP300'e girecek coinler.
    audit_priority = {
        "LIVE_OUTSIDE_BUT_CORRECTED_TOP300": 0,
        "LIVE_OUTSIDE_CORRECTED_ABOVE_MIN_NOT_TOP300": 1,
        "BELOW_CORRECTED_MIN_VOLUME": 2,
    }
    outside.sort(
        key=lambda row: (
            audit_priority.get(row["volume_audit_class"], 9),
            -row["corrected_quote_notional_24h"],
            -row["legacy_premium_volume_24h"],
            row["symbol"],
        )
    )

    return {
        "eligible": enriched,
        "premium_symbols": live_reference_symbols,  # geriye uyum
        "live_reference_symbols": live_reference_symbols,
        "corrected_symbols": corrected_symbols,
        "outside": outside,
        "volume_eligible_count": len(legacy_rows),  # geriye uyum
        "legacy_volume_eligible_count": len(legacy_rows),
        "corrected_volume_eligible_count": len(corrected_rows),
        "forced_priority_symbols": forced_priority_symbols,
    }


def hot_score(row: Dict[str, Any]) -> float:
    # Audit mismatch coinleri önce derin tara; sonra düzeltilmiş notional + hareket.
    audit_bonus = (
        100.0
        if row.get("volume_audit_class") == "LIVE_OUTSIDE_BUT_CORRECTED_TOP300"
        else 20.0
        if row.get("volume_audit_class")
        == "LIVE_OUTSIDE_CORRECTED_ABOVE_MIN_NOT_TOP300"
        else 0.0
    )
    volume = max(
        0.0,
        safe_float(row.get("corrected_quote_notional_24h"), 0.0) or 0.0,
    )
    move = abs(safe_float(row.get("change_24h_percent"), 0.0) or 0.0)
    return audit_bonus + math.log10(volume + 1.0) + min(move, 50.0) * 0.20


def select_deep_scan(
    outside: Sequence[Dict[str, Any]],
    cursor: int,
    max_per_run: int = MAX_DEEP_SCAN_PER_RUN,
    hot_count: int = HOT_CANDIDATES_PER_RUN,
) -> Tuple[List[Dict[str, Any]], int]:
    rows = [dict(row) for row in outside]
    if not rows or max_per_run <= 0:
        return [], 0

    if len(rows) <= max_per_run:
        return rows, 0

    hot = sorted(
        rows,
        key=lambda row: (hot_score(row), row.get("quote_volume_24h", 0.0)),
        reverse=True,
    )[: min(hot_count, max_per_run)]

    selected_symbols = {row["symbol"] for row in hot}
    rotation_slots = max(0, max_per_run - len(hot))

    ordered = sorted(rows, key=lambda row: row["symbol"])
    n = len(ordered)
    start = cursor % n

    rotation = []
    inspected = 0
    index = start
    while len(rotation) < rotation_slots and inspected < n:
        row = ordered[index]
        if row["symbol"] not in selected_symbols:
            rotation.append(row)
            selected_symbols.add(row["symbol"])
        index = (index + 1) % n
        inspected += 1

    chosen = hot + rotation
    next_cursor = index
    return chosen[:max_per_run], next_cursor


# ---------------------------------------------------------------------
# Runtime bağımlılıkları ve piyasa koruması
# ---------------------------------------------------------------------

def get_runtime():
    import ccxt
    import pandas as pd
    from config import (
        MAX_SCAN_COINS,
        MIN_24H_QUOTE_VOLUME,
        PRIORITY_COINS,
        ALLOW_LONG,
        ALLOW_SHORT,
        RADAR_TIMEFRAME,
        ENTRY_TIMEFRAME,
        CONFIRM_TIMEFRAME,
        TREND_TIMEFRAME,
        RADAR_LIMIT,
        ENTRY_LIMIT,
        CONFIRM_LIMIT,
        TREND_LIMIT,
        MAX_ENTRY_DISTANCE_PERCENT,
        MAX_TP1_PROGRESS_PERCENT,
        MARKET_GUARD_ENABLED,
        MARKET_REFERENCE_COINS,
        MARKET_LONG_MIN_OK_COUNT,
        MARKET_SHORT_MIN_OK_COUNT,
        MARKET_MAX_COUNTER_5M_MOVE_PERCENT,
    )
    from strategy import analyze_mtf_trade, analyze_5m_radar

    return {
        "ccxt": ccxt,
        "pd": pd,
        "MAX_SCAN_COINS": MAX_SCAN_COINS,
        "MIN_24H_QUOTE_VOLUME": MIN_24H_QUOTE_VOLUME,
        "PRIORITY_COINS": PRIORITY_COINS,
        "ALLOW_LONG": ALLOW_LONG,
        "ALLOW_SHORT": ALLOW_SHORT,
        "RADAR_TIMEFRAME": RADAR_TIMEFRAME,
        "ENTRY_TIMEFRAME": ENTRY_TIMEFRAME,
        "CONFIRM_TIMEFRAME": CONFIRM_TIMEFRAME,
        "TREND_TIMEFRAME": TREND_TIMEFRAME,
        "RADAR_LIMIT": RADAR_LIMIT,
        "ENTRY_LIMIT": ENTRY_LIMIT,
        "CONFIRM_LIMIT": CONFIRM_LIMIT,
        "TREND_LIMIT": TREND_LIMIT,
        "MAX_ENTRY_DISTANCE_PERCENT": MAX_ENTRY_DISTANCE_PERCENT,
        "MAX_TP1_PROGRESS_PERCENT": MAX_TP1_PROGRESS_PERCENT,
        "MARKET_GUARD_ENABLED": MARKET_GUARD_ENABLED,
        "MARKET_REFERENCE_COINS": MARKET_REFERENCE_COINS,
        "MARKET_LONG_MIN_OK_COUNT": MARKET_LONG_MIN_OK_COUNT,
        "MARKET_SHORT_MIN_OK_COUNT": MARKET_SHORT_MIN_OK_COUNT,
        "MARKET_MAX_COUNTER_5M_MOVE_PERCENT": MARKET_MAX_COUNTER_5M_MOVE_PERCENT,
        "analyze_mtf_trade": analyze_mtf_trade,
        "analyze_5m_radar": analyze_5m_radar,
    }


def get_exchange(runtime: Dict[str, Any]):
    return runtime["ccxt"].okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


def fetch_df(
    exchange,
    runtime: Dict[str, Any],
    symbol: str,
    timeframe: str,
    limit: int,
    min_len: int = 30,
):
    try:
        ohlcv = exchange.fetch_ohlcv(
            okx_symbol(symbol),
            timeframe=timeframe,
            limit=int(limit),
        )
        if not ohlcv or len(ohlcv) < min_len:
            return None

        return runtime["pd"].DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"],
        )
    except Exception as exc:
        print(symbol, timeframe, "veri hatası:", exc)
        return None


def simple_ema(series, span: int):
    return series.ewm(span=span, adjust=False).mean()


def market_direction_status(exchange, runtime: Dict[str, Any]) -> Dict[str, Any]:
    if not runtime["MARKET_GUARD_ENABLED"]:
        return {
            "LONG": True,
            "SHORT": True,
            "reason": "Market koruma kapalı",
        }

    long_ok = 0
    short_ok = 0
    hard_red = 0
    hard_green = 0
    details = []

    for ref_symbol in runtime["MARKET_REFERENCE_COINS"]:
        try:
            df15 = fetch_df(
                exchange, runtime, ref_symbol,
                runtime["ENTRY_TIMEFRAME"], 80, min_len=40,
            )
            df5 = fetch_df(
                exchange, runtime, ref_symbol,
                runtime["RADAR_TIMEFRAME"], 40, min_len=20,
            )
            if df15 is None or df5 is None:
                continue

            frame = df15.copy()
            frame["ema20"] = simple_ema(frame["close"], 20)
            last15 = frame.iloc[-2]
            last5 = df5.iloc[-2]

            close15 = float(last15["close"])
            ema20 = float(last15["ema20"])
            move5 = (
                (float(last5["close"]) - float(last5["open"]))
                / float(last5["open"]) * 100.0
            )

            ref_long_ok = (
                close15 >= ema20
                and move5 > -runtime["MARKET_MAX_COUNTER_5M_MOVE_PERCENT"]
            )
            ref_short_ok = (
                close15 <= ema20
                and move5 < runtime["MARKET_MAX_COUNTER_5M_MOVE_PERCENT"]
            )
            long_ok += int(ref_long_ok)
            short_ok += int(ref_short_ok)
            hard_red += int(
                move5 <= -runtime["MARKET_MAX_COUNTER_5M_MOVE_PERCENT"]
            )
            hard_green += int(
                move5 >= runtime["MARKET_MAX_COUNTER_5M_MOVE_PERCENT"]
            )
            details.append({
                "symbol": ref_symbol,
                "close15_vs_ema20": "ABOVE" if close15 >= ema20 else "BELOW",
                "move5_percent": round(move5, 4),
            })
        except Exception as exc:
            print(ref_symbol, "market koruma veri hatası:", exc)

    return {
        "LONG": (
            long_ok >= runtime["MARKET_LONG_MIN_OK_COUNT"]
            and hard_red < 2
        ),
        "SHORT": (
            short_ok >= runtime["MARKET_SHORT_MIN_OK_COUNT"]
            and hard_green < 2
        ),
        "long_ok": long_ok,
        "short_ok": short_ok,
        "hard_red": hard_red,
        "hard_green": hard_green,
        "details": details,
    }


def entry_still_valid(
    signal: Dict[str, Any],
    current_price: Optional[float],
    max_distance: float,
    max_tp1_progress: float,
) -> Tuple[bool, str]:
    try:
        entry = float(signal["entry"])
        tp1 = float(signal["tp1"])
        sl = float(signal["sl"])
        direction = str(signal["direction"]).upper()
        if current_price is None or entry <= 0:
            return False, "CURRENT_PRICE_MISSING"

        entry_distance = abs((current_price - entry) / entry) * 100.0
        if entry_distance > max_distance:
            return False, "ENTRY_TOO_FAR"

        if direction == "LONG":
            total = tp1 - entry
            progressed = current_price - entry
            if total <= 0:
                return False, "INVALID_TP1"
            progress = progressed / total * 100.0
            if progress >= max_tp1_progress or current_price >= tp1:
                return False, "TP1_TOO_ADVANCED"
            if current_price <= sl:
                return False, "PRICE_AT_SL_SIDE"

        elif direction == "SHORT":
            total = entry - tp1
            progressed = entry - current_price
            if total <= 0:
                return False, "INVALID_TP1"
            progress = progressed / total * 100.0
            if progress >= max_tp1_progress or current_price <= tp1:
                return False, "TP1_TOO_ADVANCED"
            if current_price >= sl:
                return False, "PRICE_AT_SL_SIDE"
        else:
            return False, "UNKNOWN_DIRECTION"

        return True, "OK"
    except Exception:
        return False, "ENTRY_VALIDATION_ERROR"


# ---------------------------------------------------------------------
# State / ledger
# ---------------------------------------------------------------------

def empty_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "rotation_cursor": 0,
        "coverage": {},
        "last_universe": {},
        "last_run": 0,
        "last_run_tr": None,
    }


def empty_ledger() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "trades": {},
        "summary": {},
        "last_update": 0,
        "last_update_tr": None,
    }


def ensure_state(data: Dict[str, Any]) -> Dict[str, Any]:
    state = data if isinstance(data, dict) else {}
    state.setdefault("version", VERSION)
    state.setdefault("mode", MODE)
    state.setdefault("rotation_cursor", 0)
    state.setdefault("coverage", {})
    state.setdefault("last_universe", {})
    if not isinstance(state["coverage"], dict):
        state["coverage"] = {}
    return state


def ensure_ledger(data: Dict[str, Any]) -> Dict[str, Any]:
    ledger = data if isinstance(data, dict) else {}
    ledger.setdefault("version", VERSION)
    ledger.setdefault("mode", MODE)
    ledger.setdefault("trades", {})
    ledger.setdefault("summary", {})
    if not isinstance(ledger["trades"], dict):
        ledger["trades"] = {}
    return ledger


def last_symbol_trade(
    ledger: Dict[str, Any],
    symbol: str,
) -> Optional[Dict[str, Any]]:
    matches = [
        trade
        for trade in ledger.get("trades", {}).values()
        if isinstance(trade, dict)
        and str(trade.get("symbol") or "") == symbol
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda t: max(
            safe_int(t.get("opened_at"), 0),
            safe_int(t.get("closed_at"), 0),
        )
    )
    return matches[-1]


def can_open_shadow(
    ledger: Dict[str, Any],
    symbol: str,
    direction: str,
    ts: int,
) -> Tuple[bool, str]:
    open_trades = [
        t for t in ledger.get("trades", {}).values()
        if isinstance(t, dict)
        and str(t.get("status") or "") == "OPEN"
    ]
    if len(open_trades) >= MAX_OPEN_SHADOW_TRADES:
        return False, "SHADOW_OPEN_LIMIT"

    for trade in open_trades:
        if str(trade.get("symbol") or "") == symbol:
            return False, "SAME_SYMBOL_OPEN"

    last = last_symbol_trade(ledger, symbol)
    if not last:
        return True, "OK"

    opened = safe_int(last.get("opened_at"), 0)
    closed = safe_int(last.get("closed_at"), 0)

    if (
        str(last.get("direction") or "") == direction
        and opened > 0
        and ts - opened < DUPLICATE_SECONDS
    ):
        return False, "DUPLICATE_90M"

    if closed > 0:
        if (
            str(last.get("final_result") or "") == "SL"
            and ts - closed < STOP_COOLDOWN_SECONDS
        ):
            return False, "STOP_COOLDOWN_6H"
        if ts - closed < RECENT_CLOSED_COOLDOWN_SECONDS:
            return False, "RECENT_CLOSED_4H"

    return True, "OK"


def make_trade_id(signal: Dict[str, Any], ts: int) -> str:
    return (
        f"{signal.get('symbol', 'UNKNOWN')}_"
        f"{signal.get('direction', 'UNKNOWN')}_"
        f"{signal.get('source', 'MTF')}_OUTSIDE300_{ts}"
    )


def copy_signal_fields(signal: Dict[str, Any]) -> Dict[str, Any]:
    allowed = (
        "symbol", "direction", "source", "signal_class", "score",
        "entry", "tp1", "tp2", "tp3", "sl", "risk_percent",
        "leverage", "adx_4h", "adx_1h", "adx_15m",
        "rsi_4h", "rsi_1h", "rsi_15m", "rsi_5m",
        "vol_15m", "vol_5m", "volume_ratio_15m", "volume_ratio_5m",
        "entry_distance_percent", "entry_distance_at_send_percent",
        "zone_distance_percent", "zone_drift_percent",
        "dist_1h_ema20", "dist_15m_ema20",
        "reason", "trend_reason", "confirm_reason",
    )
    return {
        key: signal.get(key)
        for key in allowed
        if key in signal
    }


def open_shadow_trade(
    ledger: Dict[str, Any],
    signal: Dict[str, Any],
    universe_row: Dict[str, Any],
    market_status: Dict[str, Any],
    ts: int,
) -> str:
    trade_id = make_trade_id(signal, ts)
    risk = abs(
        float(signal["entry"]) - float(signal["sl"])
    )

    trade = {
        **copy_signal_fields(signal),
        "trade_id": trade_id,
        "stage": "VIRTUAL_OUTSIDE300",
        "universe": "OUTSIDE300",
        "outside_reason": universe_row.get("outside_reason"),
        "volume_audit_class": universe_row.get("volume_audit_class"),
        "quote_volume_24h_at_open": universe_row.get("quote_volume_24h"),
        "legacy_premium_volume_24h_at_open": universe_row.get("legacy_premium_volume_24h"),
        "corrected_quote_notional_24h_at_open": universe_row.get("corrected_quote_notional_24h"),
        "volume_rank_all_eligible_at_open": universe_row.get("volume_rank_all_eligible"),
        "corrected_volume_rank_all_eligible_at_open": universe_row.get("corrected_volume_rank_all_eligible"),
        "change_24h_percent_at_open": universe_row.get("change_24h_percent"),
        "market_guard": {
            "long_allowed": market_status.get("LONG"),
            "short_allowed": market_status.get("SHORT"),
            "long_ok": market_status.get("long_ok"),
            "short_ok": market_status.get("short_ok"),
            "hard_red": market_status.get("hard_red"),
            "hard_green": market_status.get("hard_green"),
        },
        "opened_at": ts,
        "opened_at_tr": tr_text(ts),
        "opened_day": day_key(ts),
        "status": "OPEN",
        "events": [],
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "tp1_hit_at": 0,
        "last_checked_at": ts,
        "best_favorable_r": 0.0,
        "worst_adverse_r": 0.0,
        "risk_abs": risk,
        "final_result": None,
        "r_result": None,
        "closed_at": 0,
        "closed_day": None,
        "exit_price": None,
    }
    ledger["trades"][trade_id] = trade
    return trade_id


# ---------------------------------------------------------------------
# Sanal işlem takip / exact R
# ---------------------------------------------------------------------

def target_r(trade: Dict[str, Any], key: str) -> Optional[float]:
    entry = safe_float(trade.get("entry"), None)
    sl = safe_float(trade.get("sl"), None)
    target = safe_float(trade.get(key), None)
    if entry is None or sl is None or target is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    return abs(target - entry) / risk


def append_event(
    trade: Dict[str, Any],
    event: str,
    price: float,
    ts: int,
) -> None:
    events = trade.setdefault("events", [])
    if any(str(item.get("event")) == event for item in events if isinstance(item, dict)):
        return
    events.append({
        "event": event,
        "price": round(float(price), 12),
        "time": ts,
        "time_tr": tr_text(ts),
    })


def close_trade(
    trade: Dict[str, Any],
    result: str,
    exit_price: float,
    ts: int,
) -> None:
    tp1_r = target_r(trade, "tp1")
    tp3_r = target_r(trade, "tp3")

    if result == "SL":
        final = "SL"
        r_value = -1.0

    elif result == "BE":
        final = (
            "TP2_SONRASI_BE"
            if trade.get("tp2_hit")
            else "TP1_SONRASI_BE"
        )
        r_value = (
            round(0.50 * tp1_r, 4)
            if tp1_r is not None
            else None
        )

    elif result == "TP3":
        final = "TP3"
        r_value = (
            round(0.50 * tp1_r + 0.50 * tp3_r, 4)
            if tp1_r is not None and tp3_r is not None
            else None
        )

    elif result == "EXPIRED":
        final = "EXPIRED"
        entry = safe_float(trade.get("entry"), None)
        sl = safe_float(trade.get("sl"), None)
        risk = abs(entry - sl) if entry is not None and sl is not None else 0.0
        if entry is None or risk <= 0:
            r_value = None
        else:
            direction = str(trade.get("direction") or "")
            remaining_r = (
                (exit_price - entry) / risk
                if direction == "LONG"
                else (entry - exit_price) / risk
            )
            if trade.get("tp1_hit") and tp1_r is not None:
                r_value = round(0.50 * tp1_r + 0.50 * remaining_r, 4)
            else:
                r_value = round(remaining_r, 4)
    else:
        return

    trade["status"] = "CLOSED"
    trade["final_result"] = final
    trade["r_result"] = r_value
    trade["exit_price"] = round(float(exit_price), 12)
    trade["closed_at"] = ts
    trade["closed_at_tr"] = tr_text(ts)
    trade["closed_day"] = day_key(ts)


def update_excursion(
    trade: Dict[str, Any],
    high: float,
    low: float,
) -> None:
    entry = safe_float(trade.get("entry"), None)
    sl = safe_float(trade.get("sl"), None)
    if entry is None or sl is None:
        return

    risk = abs(entry - sl)
    if risk <= 0:
        return

    direction = str(trade.get("direction") or "")
    if direction == "LONG":
        favorable = max(0.0, (high - entry) / risk)
        adverse = max(0.0, (entry - low) / risk)
    else:
        favorable = max(0.0, (entry - low) / risk)
        adverse = max(0.0, (high - entry) / risk)

    trade["best_favorable_r"] = round(
        max(safe_float(trade.get("best_favorable_r"), 0.0) or 0.0, favorable),
        4,
    )
    trade["worst_adverse_r"] = round(
        max(safe_float(trade.get("worst_adverse_r"), 0.0) or 0.0, adverse),
        4,
    )


def process_trade_candles(
    trade: Dict[str, Any],
    candles: Sequence[Dict[str, Any]],
    current_ts: int,
) -> bool:
    """
    Premium ana botun hedef sırasına yakın davranır.
    Aynı mumda SL ve TP1 birlikte görülürse mum kapanışı yön tercihini belirler.
    TP1 görülen aynı mumda BE kapatılmaz.
    """
    if str(trade.get("status") or "") != "OPEN":
        return False

    entry = float(trade["entry"])
    tp1 = float(trade["tp1"])
    tp2 = float(trade["tp2"])
    tp3 = float(trade["tp3"])
    sl = float(trade["sl"])
    direction = str(trade["direction"]).upper()

    tp1_hit = bool(trade.get("tp1_hit"))
    tp2_hit = bool(trade.get("tp2_hit"))
    tp3_hit = bool(trade.get("tp3_hit"))
    tp1_hit_at = safe_int(trade.get("tp1_hit_at"), 0)
    last_checked = safe_int(trade.get("last_checked_at"), safe_int(trade.get("opened_at"), 0))
    changed = False

    # Açılışın bulunduğu 1M mumu değil, ilk tam sonraki mumu kullan.
    first_full_candle = (
        (safe_int(trade.get("opened_at"), 0) // 60) * 60 + 60
    )

    ordered = sorted(
        [c for c in candles if isinstance(c, dict)],
        key=lambda c: safe_int(c.get("time"), 0),
    )

    for candle in ordered:
        candle_time = safe_int(candle.get("time"), 0)
        if candle_time < first_full_candle:
            continue
        if candle_time <= last_checked:
            continue
        # Henüz kapanmamış mevcut 1M mumu kullanma.
        if candle_time + 60 > current_ts:
            continue

        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        update_excursion(trade, high, low)

        just_hit_tp1 = False

        if direction == "LONG":
            if not tp1_hit:
                if low <= sl and high >= tp1:
                    if close >= entry:
                        tp1_hit = True
                        just_hit_tp1 = True
                        tp1_hit_at = candle_time
                        append_event(trade, "TP1", tp1, candle_time)
                    else:
                        append_event(trade, "SL", sl, candle_time)
                        close_trade(trade, "SL", close, candle_time)
                        changed = True
                        break
                elif low <= sl:
                    append_event(trade, "SL", sl, candle_time)
                    close_trade(trade, "SL", close, candle_time)
                    changed = True
                    break
                elif high >= tp1:
                    tp1_hit = True
                    just_hit_tp1 = True
                    tp1_hit_at = candle_time
                    append_event(trade, "TP1", tp1, candle_time)

            if tp1_hit:
                if not tp2_hit and high >= tp2:
                    tp2_hit = True
                    append_event(trade, "TP2", tp2, candle_time)
                if not tp3_hit and high >= tp3:
                    tp3_hit = True
                    append_event(trade, "TP3", tp3, candle_time)
                    close_trade(trade, "TP3", tp3, candle_time)
                    changed = True
                    break
                if (
                    not just_hit_tp1
                    and candle_time > tp1_hit_at
                    and low <= entry
                ):
                    append_event(trade, "BE", entry, candle_time)
                    close_trade(trade, "BE", entry, candle_time)
                    changed = True
                    break

        elif direction == "SHORT":
            if not tp1_hit:
                if high >= sl and low <= tp1:
                    if close <= entry:
                        tp1_hit = True
                        just_hit_tp1 = True
                        tp1_hit_at = candle_time
                        append_event(trade, "TP1", tp1, candle_time)
                    else:
                        append_event(trade, "SL", sl, candle_time)
                        close_trade(trade, "SL", close, candle_time)
                        changed = True
                        break
                elif high >= sl:
                    append_event(trade, "SL", sl, candle_time)
                    close_trade(trade, "SL", close, candle_time)
                    changed = True
                    break
                elif low <= tp1:
                    tp1_hit = True
                    just_hit_tp1 = True
                    tp1_hit_at = candle_time
                    append_event(trade, "TP1", tp1, candle_time)

            if tp1_hit:
                if not tp2_hit and low <= tp2:
                    tp2_hit = True
                    append_event(trade, "TP2", tp2, candle_time)
                if not tp3_hit and low <= tp3:
                    tp3_hit = True
                    append_event(trade, "TP3", tp3, candle_time)
                    close_trade(trade, "TP3", tp3, candle_time)
                    changed = True
                    break
                if (
                    not just_hit_tp1
                    and candle_time > tp1_hit_at
                    and high >= entry
                ):
                    append_event(trade, "BE", entry, candle_time)
                    close_trade(trade, "BE", entry, candle_time)
                    changed = True
                    break

        trade["last_checked_at"] = candle_time
        changed = True

    trade["tp1_hit"] = tp1_hit
    trade["tp2_hit"] = tp2_hit
    trade["tp3_hit"] = tp3_hit
    trade["tp1_hit_at"] = tp1_hit_at

    if str(trade.get("status") or "") == "OPEN":
        age = current_ts - safe_int(trade.get("opened_at"), current_ts)
        if age >= MAX_OPEN_HOURS * 3600:
            # Caller son fiyatı ayrıca expire_trade ile kapatabilir.
            trade["expiry_due"] = True

    return changed


def fetch_tracking_candles(
    exchange,
    trade: Dict[str, Any],
    current_ts: int,
) -> List[Dict[str, Any]]:
    try:
        since = safe_int(trade.get("last_checked_at"), safe_int(trade.get("opened_at"), current_ts))
        # Bir miktar overlap güvenlidir; process_trade_candles duplicate zamanı atlar.
        since_ms = max(0, since - 120) * 1000
        ohlcv = exchange.fetch_ohlcv(
            okx_symbol(str(trade.get("symbol"))),
            timeframe=TRACK_TIMEFRAME,
            since=since_ms,
            limit=TRACK_LIMIT,
        )
        return [
            {
                "time": int(item[0] / 1000),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
            }
            for item in (ohlcv or [])
        ]
    except Exception as exc:
        print(trade.get("symbol"), "takip mum hatası:", exc)
        return []


def expire_trade_if_due(
    trade: Dict[str, Any],
    current_price: Optional[float],
    ts: int,
) -> bool:
    if str(trade.get("status") or "") != "OPEN":
        return False
    age = ts - safe_int(trade.get("opened_at"), ts)
    if age < MAX_OPEN_HOURS * 3600:
        return False
    if current_price is None:
        return False
    append_event(trade, "EXPIRED", current_price, ts)
    close_trade(trade, "EXPIRED", current_price, ts)
    return True


# ---------------------------------------------------------------------
# Özet
# ---------------------------------------------------------------------

def volume_band(volume: Any) -> str:
    value = safe_float(volume, 0.0) or 0.0
    if value < 100_000:
        return "LT_100K"
    if value < 500_000:
        return "100K_TO_500K"
    return "GTE_500K_OUTSIDE300"


def rank_band(rank: Any) -> str:
    value = safe_int(rank, 0)
    if value <= 0:
        return "UNKNOWN"
    if value <= 350:
        return "RANK_301_350"
    if value <= 400:
        return "RANK_351_400"
    return "RANK_401_PLUS"


def aggregate_group(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [
        t for t in trades
        if str(t.get("status") or "") == "CLOSED"
        and safe_float(t.get("r_result"), None) is not None
    ]
    net_r = round(sum(float(t["r_result"]) for t in closed), 4) if closed else 0.0
    positive = sum(1 for t in closed if float(t["r_result"]) > 0)
    finals = Counter(str(t.get("final_result") or "") for t in closed)
    tp1_hits = sum(1 for t in trades if bool(t.get("tp1_hit")))
    tp2_hits = sum(1 for t in trades if bool(t.get("tp2_hit")))
    tp3_hits = sum(1 for t in trades if bool(t.get("tp3_hit")))

    return {
        "total": len(trades),
        "open": sum(1 for t in trades if str(t.get("status") or "") == "OPEN"),
        "closed": len(closed),
        "tp1_hit": tp1_hits,
        "tp2_hit": tp2_hits,
        "tp3_hit": tp3_hits,
        "tp1_rate_percent": pct(tp1_hits, len(trades)),
        "tp3_rate_percent": pct(tp3_hits, len(trades)),
        "final_results": dict(finals),
        "positive_closed": positive,
        "positive_rate_percent": pct(positive, len(closed)),
        "net_r": net_r,
        "avg_r": round(net_r / len(closed), 4) if closed else 0.0,
    }


def build_summary(ledger: Dict[str, Any]) -> Dict[str, Any]:
    trades = [
        t for t in ledger.get("trades", {}).values()
        if isinstance(t, dict)
    ]

    by_reason = defaultdict(list)
    by_direction = defaultdict(list)
    by_source = defaultdict(list)
    by_volume = defaultdict(list)
    by_rank = defaultdict(list)
    by_day = defaultdict(list)

    for trade in trades:
        by_reason[str(trade.get("outside_reason") or "UNKNOWN")].append(trade)
        by_direction[str(trade.get("direction") or "UNKNOWN")].append(trade)
        by_source[str(trade.get("source") or "UNKNOWN")].append(trade)
        by_volume[volume_band(trade.get("quote_volume_24h_at_open"))].append(trade)
        by_rank[rank_band(trade.get("volume_rank_all_eligible_at_open"))].append(trade)
        by_day[str(trade.get("opened_day") or "UNKNOWN")].append(trade)

    return {
        "overall": aggregate_group(trades),
        "by_outside_reason": {
            key: aggregate_group(value)
            for key, value in sorted(by_reason.items())
        },
        "by_direction": {
            key: aggregate_group(value)
            for key, value in sorted(by_direction.items())
        },
        "by_source": {
            key: aggregate_group(value)
            for key, value in sorted(by_source.items())
        },
        "by_volume_band": {
            key: aggregate_group(value)
            for key, value in sorted(by_volume.items())
        },
        "by_volume_rank": {
            key: aggregate_group(value)
            for key, value in sorted(by_rank.items())
        },
        "by_open_day": {
            key: aggregate_group(value)
            for key, value in sorted(by_day.items())
        },
    }


# ---------------------------------------------------------------------
# Derin tarama
# ---------------------------------------------------------------------

def analyze_one(
    exchange,
    runtime: Dict[str, Any],
    row: Dict[str, Any],
    market_status: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str]:
    symbol = row["symbol"]
    current_price = safe_float(row.get("last"), None)
    if current_price is None:
        return None, "NO_CURRENT_PRICE"

    df15 = fetch_df(
        exchange, runtime, symbol,
        runtime["ENTRY_TIMEFRAME"], runtime["ENTRY_LIMIT"], min_len=220,
    )
    df1h = fetch_df(
        exchange, runtime, symbol,
        runtime["CONFIRM_TIMEFRAME"], runtime["CONFIRM_LIMIT"], min_len=220,
    )
    df4h = fetch_df(
        exchange, runtime, symbol,
        runtime["TREND_TIMEFRAME"], runtime["TREND_LIMIT"], min_len=220,
    )

    if df15 is None or df1h is None or df4h is None:
        return None, "INSUFFICIENT_MTF_HISTORY"

    candidates = []

    normal = runtime["analyze_mtf_trade"](
        symbol, df15, df1h, df4h, current_price,
    )
    if isinstance(normal, dict):
        candidates.append(normal)

    df5 = fetch_df(
        exchange, runtime, symbol,
        runtime["RADAR_TIMEFRAME"], runtime["RADAR_LIMIT"], min_len=220,
    )
    if df5 is not None:
        radar = runtime["analyze_5m_radar"](
            symbol, df5, df15, df1h, df4h, current_price,
        )
        if isinstance(radar, dict):
            candidates.append(radar)

    trade_candidates = [
        signal for signal in candidates
        if str(signal.get("signal_class") or "TRADE").upper() == "TRADE"
    ]
    if not trade_candidates:
        return None, "NO_PREMIUM_TRADE_SETUP"

    valid = []
    reject_reasons = Counter()
    for signal in trade_candidates:
        direction = str(signal.get("direction") or "").upper()
        if direction == "LONG" and not runtime["ALLOW_LONG"]:
            reject_reasons["LONG_DISABLED"] += 1
            continue
        if direction == "SHORT" and not runtime["ALLOW_SHORT"]:
            reject_reasons["SHORT_DISABLED"] += 1
            continue
        if not market_status.get(direction, True):
            reject_reasons["MARKET_GUARD"] += 1
            continue

        ok, reason = entry_still_valid(
            signal,
            current_price,
            runtime["MAX_ENTRY_DISTANCE_PERCENT"],
            runtime["MAX_TP1_PROGRESS_PERCENT"],
        )
        if not ok:
            reject_reasons[reason] += 1
            continue
        valid.append(signal)

    if not valid:
        if reject_reasons:
            return None, reject_reasons.most_common(1)[0][0]
        return None, "NO_VALID_TRADE"

    # Aynı coinde tek sanal işlem: skor yüksek olan; eşitlikte 15M_ENTRY öncelikli.
    valid.sort(
        key=lambda s: (
            safe_float(s.get("score"), 0.0) or 0.0,
            1 if str(s.get("source") or "") == "15M_ENTRY" else 0,
        ),
        reverse=True,
    )
    return valid[0], "VIRTUAL_SIGNAL"


# ---------------------------------------------------------------------
# Ana çalışma
# ---------------------------------------------------------------------

def run_once() -> Dict[str, Any]:
    ts = now_ts()
    runtime = get_runtime()
    exchange = get_exchange(runtime)

    state = ensure_state(load_json(STATE_FILE, empty_state()))
    ledger = ensure_ledger(load_json(LEDGER_FILE, empty_ledger()))

    print("=" * 82)
    print("TÜM PİYASA KEŞİF + SANAL PERFORMANS V1")
    print("Mod:", MODE)
    print("=" * 82)

    # 1) Önce mevcut açık sanal işlemleri takip et.
    tickers_cache: Dict[str, Dict[str, Any]] = {}
    tracking_updates = 0
    closed_now = 0

    open_trades = [
        trade
        for trade in ledger["trades"].values()
        if isinstance(trade, dict)
        and str(trade.get("status") or "") == "OPEN"
    ]
    for trade in open_trades:
        candles = fetch_tracking_candles(exchange, trade, ts)
        if process_trade_candles(trade, candles, ts):
            tracking_updates += 1
        if str(trade.get("status") or "") == "CLOSED":
            closed_now += 1
            continue

        if trade.get("expiry_due"):
            symbol_okx = okx_symbol(str(trade.get("symbol")))
            try:
                ticker = exchange.fetch_ticker(symbol_okx)
                current = safe_float(ticker.get("last"), None)
            except Exception:
                current = None
            if expire_trade_if_due(trade, current, ts):
                closed_now += 1

    # 2) Bütün uygun market + ticker evreni.
    markets = exchange.load_markets()
    eligible = eligible_markets(markets)
    okx_symbols = [row["okx_symbol"] for row in eligible]
    tickers = exchange.fetch_tickers(okx_symbols)

    universe = build_universe(
        markets=markets,
        tickers=tickers,
        priority_coins=runtime["PRIORITY_COINS"],
        min_quote_volume=runtime["MIN_24H_QUOTE_VOLUME"],
        max_scan_coins=runtime["MAX_SCAN_COINS"],
    )

    outside = universe["outside"]
    chosen, next_cursor = select_deep_scan(
        outside,
        safe_int(state.get("rotation_cursor"), 0),
    )
    state["rotation_cursor"] = next_cursor

    market_status = market_direction_status(exchange, runtime)

    reason_counts = Counter()
    virtual_opened = 0

    coverage = state["coverage"]
    for row in chosen:
        symbol = row["symbol"]
        item = coverage.setdefault(symbol, {
            "deep_scan_count": 0,
            "qualified_count": 0,
            "last_deep_scan_at": 0,
            "last_result": None,
        })
        item["deep_scan_count"] = safe_int(item.get("deep_scan_count"), 0) + 1
        item["last_deep_scan_at"] = ts
        item["last_deep_scan_at_tr"] = tr_text(ts)
        item["outside_reason"] = row.get("outside_reason")
        item["volume_audit_class"] = row.get("volume_audit_class")
        item["quote_volume_24h"] = row.get("quote_volume_24h")
        item["legacy_premium_volume_24h"] = row.get("legacy_premium_volume_24h")
        item["corrected_quote_notional_24h"] = row.get("corrected_quote_notional_24h")
        item["volume_rank_all_eligible"] = row.get("volume_rank_all_eligible")
        item["corrected_volume_rank_all_eligible"] = row.get("corrected_volume_rank_all_eligible")

        signal, reason = analyze_one(
            exchange, runtime, row, market_status,
        )
        item["last_result"] = reason
        reason_counts[reason] += 1

        if not signal:
            continue

        direction = str(signal.get("direction") or "").upper()
        can_open, block_reason = can_open_shadow(
            ledger, symbol, direction, ts,
        )
        if not can_open:
            reason_counts[block_reason] += 1
            item["last_result"] = block_reason
            continue

        open_shadow_trade(
            ledger, signal, row, market_status, ts,
        )
        item["qualified_count"] = safe_int(item.get("qualified_count"), 0) + 1
        item["last_result"] = "VIRTUAL_OPENED"
        virtual_opened += 1

    outside_below = sum(
        1 for row in outside
        if row.get("outside_reason") == "BELOW_PREMIUM_MIN_VOLUME_LEGACY"
    )
    outside_rank = sum(
        1 for row in outside
        if row.get("outside_reason") == "OUTSIDE_PREMIUM_TOP300_LEGACY"
    )
    audit_mismatch = [
        row for row in outside
        if row.get("volume_audit_class") == "LIVE_OUTSIDE_BUT_CORRECTED_TOP300"
    ]
    corrected_overflow = [
        row for row in outside
        if row.get("volume_audit_class")
        == "LIVE_OUTSIDE_CORRECTED_ABOVE_MIN_NOT_TOP300"
    ]
    true_low_volume = [
        row for row in outside
        if row.get("volume_audit_class") == "BELOW_CORRECTED_MIN_VOLUME"
    ]

    covered_outside = sum(
        1 for row in outside
        if safe_int(
            coverage.get(row["symbol"], {}).get("deep_scan_count"),
            0,
        ) > 0
    )

    state["last_universe"] = {
        "captured_at": ts,
        "captured_at_tr": tr_text(ts),
        "eligible_usdt_swap_total": len(universe["eligible"]),
        "premium_top300_count": len(universe["premium_symbols"]),
        "live_reference_count": len(universe["live_reference_symbols"]),
        "corrected_top300_count": len(universe["corrected_symbols"]),
        "volume_above_premium_min_count": universe["volume_eligible_count"],
        "legacy_volume_above_min_count": universe["legacy_volume_eligible_count"],
        "corrected_notional_above_min_count": universe["corrected_volume_eligible_count"],
        "priority_forced_live_count": len(
            universe.get("forced_priority_symbols", [])
        ),
        "priority_forced_live_symbols": universe.get(
            "forced_priority_symbols",
            [],
        ),
        "outside300_total": len(outside),
        "outside_below_premium_min_volume": outside_below,
        "outside_top300_rank_overflow": outside_rank,
        "volume_audit_mismatch_count": len(audit_mismatch),
        "corrected_above_min_but_not_top300_count": len(corrected_overflow),
        "true_below_corrected_min_volume_count": len(true_low_volume),
        "volume_audit_mismatch_symbols": [
            row["symbol"] for row in audit_mismatch[:100]
        ],
        "deep_scanned_this_run": len(chosen),
        "covered_outside_symbols_lifetime": covered_outside,
        "coverage_percent_current_outside": pct(covered_outside, len(outside)),
        "estimated_runs_for_full_rotation": (
            math.ceil(len(outside) / max(1, MAX_DEEP_SCAN_PER_RUN - HOT_CANDIDATES_PER_RUN))
            if outside else 0
        ),
        "market_guard": market_status,
        "scan_reason_counts": dict(reason_counts),
        "sample_outside_symbols": [row["symbol"] for row in outside[:20]],
        "deep_scan_symbols": [row["symbol"] for row in chosen],
    }
    state["last_run"] = ts
    state["last_run_tr"] = tr_text(ts)
    state["version"] = VERSION
    state["mode"] = MODE

    ledger["summary"] = build_summary(ledger)
    ledger["last_update"] = ts
    ledger["last_update_tr"] = tr_text(ts)
    ledger["version"] = VERSION
    ledger["mode"] = MODE

    save_json_atomic(STATE_FILE, state)
    save_json_atomic(LEDGER_FILE, ledger)

    overall = ledger["summary"]["overall"]
    print("Uygun USDT swap:", len(universe["eligible"]))
    print("Premium canlı referans:", len(universe["premium_symbols"]))
    print("Düzeltilmiş notional TOP300:", len(universe["corrected_symbols"]))
    print("Canlı OUTSIDE:", len(outside))
    print("Hacim audit mismatch:", len(audit_mismatch))
    print("Bu tur derin tarama:", len(chosen))
    print("Yeni sanal işlem:", virtual_opened)
    print("Bu tur kapanan:", closed_now)
    print(
        "Sanal toplam:",
        overall["total"],
        "| kapalı:", overall["closed"],
        "| net:", f"{overall['net_r']:+.4f}R",
    )
    print("Telegram: YOK | Emir: YOK | Canlı filtre değişikliği: YOK")
    print("=" * 82)

    return {
        "eligible_total": len(universe["eligible"]),
        "premium_count": len(universe["premium_symbols"]),
        "corrected_top300_count": len(universe["corrected_symbols"]),
        "outside_total": len(outside),
        "volume_audit_mismatch_count": len(audit_mismatch),
        "deep_scanned": len(chosen),
        "virtual_opened": virtual_opened,
        "closed_now": closed_now,
        "net_r": overall["net_r"],
    }


def main() -> None:
    try:
        run_once()
    except Exception as exc:
        print("Tüm piyasa gölge motoru hatası:", exc)
        raise


if __name__ == "__main__":
    main()
