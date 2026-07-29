# new_listing_radar.py
# Yeni Liste Sessiz Takip + Giriş Onayı Radarı v2
#
# Amaç:
# - OKX'te son 72 saat içinde açılmış canlı USDT perpetual sözleşmeleri bulmak.
# - 1 dakikalık fiyat/hacim verisiyle erken hareketleri ve ilk geri çekilmeleri izlemek.
# - Fırsat adaylarını Telegram'a göndermeden sessizce izlemek.
# - Kırılım, hacim ve giriş yakınlığı doğrulanınca TP/SL'li giriş onayı göndermek.
#
# Bu sistem:
# - Otomatik emir açmaz.
# - Yalnız doğrulanmış adaylarda giriş, TP ve SL seviyeleri üretir.
# - Ana MTF, Scalp, Pump/Dump veya Swing state dosyalarını değiştirmez.
# - Diğer botların açık sinyal limitlerini doldurmaz.
# - Kendi state ve performans ledger dosyalarını atomik olarak yazar.

import json
import math
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import ccxt
import requests

try:
    from portfolio_risk import (
        evaluate_portfolio_risk,
        format_portfolio_note,
    )
except Exception:
    evaluate_portfolio_risk = None
    format_portfolio_note = None


BOT_NAME = "Yeni Liste Sessiz Takip + Giriş Onayı Radarı v2"

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

STATE_FILE = "new_listing_radar_state.json"
PERFORMANCE_FILE = "new_listing_performance_ledger.json"

TR_TIMEZONE = timezone(timedelta(hours=3))

MIN_LISTING_AGE_MINUTES = 20
MAX_LISTING_AGE_HOURS = 72

MIN_24H_QUOTE_VOLUME = 100_000
MAX_NEW_MARKETS_PER_RUN = 20
MAX_ALERTS_PER_RUN = 2

# V2: Telegram'a izleme mesajı gönderilmez.
# Adaylar sessizce tutulur; en az 2 dakika sonra şartlar tamamlanınca en fazla 1 giriş onayı gönderilir.
MAX_TRADE_SIGNALS_PER_RUN = 1
SILENT_CANDIDATE_EXPIRY_MINUTES = 60
MIN_CONFIRM_WATCH_AGE_MINUTES = 2
TRADE_SIGNAL_COOLDOWN_SECONDS = 4 * 60 * 60

MIN_CONFIRM_SCORE = 82
MIN_CONFIRM_VOLUME_1M = 1.10
MIN_CONFIRM_VOLUME_3M = 1.20
MIN_CONFIRM_VOLUME_5M = 1.10
MAX_CONFIRM_ENTRY_DRIFT_PERCENT = 3.0
MAX_CONFIRM_LEVEL_DISTANCE_PERCENT = 2.0
MAX_CONFIRM_5M_MOVE_PERCENT = 5.0

MIN_STOP_PERCENT = 0.90
MAX_STOP_PERCENT = 3.00
TP1_R_MULTIPLIER = 1.20
TP2_R_MULTIPLIER = 2.00
TP3_R_MULTIPLIER = 3.00

MIN_CLOSED_1M_CANDLES = 20
OHLCV_LIMIT = 180

ALERT_DUPLICATE_SECONDS = 60 * 60
SYMBOL_ALERT_COOLDOWN_SECONDS = 20 * 60

MIN_ALERT_SCORE = 72

PERFORMANCE_WINDOWS_MINUTES = (15, 30, 60, 180)
PERFORMANCE_FINAL_MINUTES = 180
PERFORMANCE_KEEP_DAYS = 14
PERFORMANCE_MAX_RECORDS = 500

REQUEST_TIMEOUT_SECONDS = 20


def now_ts():
    return int(time.time())


def now_ms():
    return int(time.time() * 1000)


def tr_now():
    return datetime.now(TR_TIMEZONE)


def iso_tr(timestamp=None):
    if timestamp is None:
        value = tr_now()
    else:
        value = datetime.fromtimestamp(
            float(timestamp),
            tz=TR_TIMEZONE,
        )

    return value.isoformat(timespec="seconds")


def safe_float(value, default=0.0):
    try:
        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except (TypeError, ValueError):
        return default


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def format_price(value):
    price = safe_float(value, 0.0)

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.5f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.10f}".rstrip("0").rstrip(".")


def format_signed_percent(value):
    if value is None:
        return "-"

    number = safe_float(value, 0.0)
    return f"{number:+.2f}%"


def format_age_minutes(age_minutes):
    total_minutes = max(
        0,
        int(round(safe_float(age_minutes, 0))),
    )

    hours, minutes = divmod(total_minutes, 60)

    if hours <= 0:
        return f"{minutes} dk"

    return f"{hours} sa {minutes} dk"


def normalize_display_symbol(symbol):
    value = str(symbol or "").upper().strip()

    if "/" in value:
        base, remainder = value.split("/", 1)
        quote = remainder.split(":", 1)[0]
        value = base + quote

    return (
        value.replace("-", "")
        .replace("_", "")
        .replace(":", "")
        .replace("/", "")
        .replace(" ", "")
    )


def percentage_change(current, previous):
    current_value = safe_float(current, 0.0)
    previous_value = safe_float(previous, 0.0)

    if current_value <= 0 or previous_value <= 0:
        return None

    return (
        (current_value / previous_value) - 1.0
    ) * 100.0


def directional_return(
    direction,
    entry_price,
    current_price,
):
    raw_return = percentage_change(
        current_price,
        entry_price,
    )

    if raw_return is None:
        return None

    if str(direction).upper() == "SHORT":
        return -raw_return

    return raw_return


def candle_close_power(candle):
    if not isinstance(candle, (list, tuple)):
        return 50.0

    if len(candle) < 5:
        return 50.0

    high = safe_float(candle[2], 0.0)
    low = safe_float(candle[3], 0.0)
    close = safe_float(candle[4], 0.0)

    candle_range = high - low

    if candle_range <= 0:
        return 50.0

    return clamp(
        ((close - low) / candle_range) * 100.0,
        0.0,
        100.0,
    )


def average(values):
    cleaned = [
        safe_float(value, 0.0)
        for value in values
        if safe_float(value, 0.0) >= 0
    ]

    if not cleaned:
        return 0.0

    return sum(cleaned) / len(cleaned)


def safe_load_json(filename, default):
    if not os.path.exists(filename):
        return default

    try:
        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as handle:
            loaded = json.load(handle)

        if isinstance(default, dict):
            return loaded if isinstance(loaded, dict) else default

        return loaded

    except Exception as exc:
        print(
            filename,
            "JSON okuma hatası:",
            exc,
        )
        return default


def fsync_parent_directory(filename):
    directory = os.path.dirname(
        os.path.abspath(filename)
    ) or "."

    directory_fd = None

    try:
        directory_fd = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(directory_fd)

    except Exception:
        pass

    finally:
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except Exception:
                pass


def atomic_save_json(filename, data):
    normalized_data = (
        data
        if isinstance(data, dict)
        else {}
    )

    absolute_filename = os.path.abspath(filename)
    directory = os.path.dirname(
        absolute_filename
    ) or "."
    base_name = os.path.basename(
        absolute_filename
    )
    temp_path = None

    try:
        os.makedirs(
            directory,
            exist_ok=True,
        )

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{base_name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name

            json.dump(
                normalized_data,
                handle,
                indent=2,
                ensure_ascii=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(
            temp_path,
            "r",
            encoding="utf-8",
        ) as verify_handle:
            verified = json.load(verify_handle)

        if not isinstance(verified, dict):
            raise ValueError(
                "Geçici JSON doğrulaması başarısız."
            )

        os.replace(
            temp_path,
            absolute_filename,
        )
        temp_path = None

        fsync_parent_directory(
            absolute_filename
        )

        return True

    except Exception as exc:
        print(
            filename,
            "atomik JSON kayıt hatası:",
            exc,
        )
        return False

    finally:
        if (
            temp_path
            and os.path.exists(temp_path)
        ):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def empty_state():
    return {
        "version": "NEW_LISTING_RADAR_V2",
        "last_run_at": None,
        "last_alerts": {},
        "last_symbol_alerts": {},
        "seen_listings": {},
        "silent_candidates": {},
        "trade_signal_cooldowns": {},
        "stats": {
            "runs": 0,
            "markets_seen": 0,
            "alerts_sent": 0,
            "candidate_created": 0,
            "candidate_updated": 0,
            "candidate_expired": 0,
            "candidate_invalidated": 0,
            "trade_signals_sent": 0,
            "portfolio_blocks": 0,
            "telegram_failures": 0,
        },
    }


def load_state():
    state = safe_load_json(
        STATE_FILE,
        empty_state(),
    )

    state.setdefault(
        "version",
        "NEW_LISTING_RADAR_V2",
    )
    state.setdefault(
        "last_alerts",
        {},
    )
    state.setdefault(
        "last_symbol_alerts",
        {},
    )
    state.setdefault(
        "seen_listings",
        {},
    )
    state.setdefault(
        "silent_candidates",
        {},
    )
    state.setdefault(
        "trade_signal_cooldowns",
        {},
    )
    state.setdefault(
        "stats",
        {},
    )

    state["version"] = "NEW_LISTING_RADAR_V2"

    for key, default_value in {
        "runs": 0,
        "markets_seen": 0,
        "alerts_sent": 0,
        "candidate_created": 0,
        "candidate_updated": 0,
        "candidate_expired": 0,
        "candidate_invalidated": 0,
        "trade_signals_sent": 0,
        "portfolio_blocks": 0,
        "telegram_failures": 0,
    }.items():
        state["stats"].setdefault(
            key,
            default_value,
        )

    return state


def save_state(state):
    return atomic_save_json(
        STATE_FILE,
        state,
    )


def empty_performance():
    return {
        "version": "NEW_LISTING_PERFORMANCE_V1",
        "updated_at": None,
        "records": {},
        "summary": {
            "total_alerts": 0,
            "tracking": 0,
            "finalized": 0,
            "positive_180": 0,
            "positive_rate_180": 0.0,
            "average_return_180": 0.0,
            "by_alert_type": {},
        },
    }


def load_performance():
    ledger = safe_load_json(
        PERFORMANCE_FILE,
        empty_performance(),
    )

    ledger.setdefault(
        "version",
        "NEW_LISTING_PERFORMANCE_V1",
    )
    ledger.setdefault(
        "records",
        {},
    )
    ledger.setdefault(
        "summary",
        {},
    )

    return ledger


def prune_performance_records(ledger):
    records = ledger.get(
        "records",
        {},
    )

    if not isinstance(records, dict):
        ledger["records"] = {}
        return

    cutoff = now_ts() - (
        PERFORMANCE_KEEP_DAYS * 24 * 60 * 60
    )

    ordered = sorted(
        records.items(),
        key=lambda item: safe_float(
            item[1].get("sent_at"),
            0,
        ),
        reverse=True,
    )

    kept = {}

    for record_id, record in ordered:
        sent_at = int(
            safe_float(
                record.get("sent_at"),
                0,
            )
        )

        if sent_at < cutoff:
            continue

        if len(kept) >= PERFORMANCE_MAX_RECORDS:
            break

        kept[record_id] = record

    ledger["records"] = kept


def rebuild_performance_summary(ledger):
    records = list(
        ledger.get(
            "records",
            {},
        ).values()
    )

    finalized = [
        record
        for record in records
        if record.get("status") == "FINAL"
    ]

    final_returns = []

    for record in finalized:
        checkpoints = record.get(
            "checkpoints",
            {},
        )
        final_checkpoint = checkpoints.get(
            str(PERFORMANCE_FINAL_MINUTES),
            {},
        )
        final_return = final_checkpoint.get(
            "directional_return_percent"
        )

        if final_return is not None:
            final_returns.append(
                safe_float(final_return, 0.0)
            )

    by_type = Counter(
        str(record.get("alert_type") or "UNKNOWN")
        for record in records
    )

    positive_count = sum(
        1
        for value in final_returns
        if value > 0
    )

    positive_rate = (
        (positive_count / len(final_returns)) * 100.0
        if final_returns
        else 0.0
    )

    ledger["summary"] = {
        "total_alerts": len(records),
        "tracking": sum(
            1
            for record in records
            if record.get("status") == "TRACKING"
        ),
        "finalized": len(finalized),
        "positive_180": positive_count,
        "positive_rate_180": round(
            positive_rate,
            2,
        ),
        "average_return_180": round(
            average(final_returns),
            4,
        ),
        "by_alert_type": dict(by_type),
    }

    ledger["updated_at"] = iso_tr()


def save_performance(ledger):
    prune_performance_records(ledger)
    rebuild_performance_summary(ledger)

    return atomic_save_json(
        PERFORMANCE_FILE,
        ledger,
    )


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print(
            "Telegram TOKEN veya CHAT_ID eksik."
        )
        return False

    url = (
        f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        print(
            "Telegram HTTP:",
            response.status_code,
        )

        return response.ok

    except Exception as exc:
        print(
            "Telegram gönderim hatası:",
            exc,
        )
        return False


def build_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "timeout": 30_000,
        "options": {
            "defaultType": "swap",
        },
    })


def market_list_time_ms(market):
    info = (
        market.get("info")
        if isinstance(market, dict)
        else {}
    ) or {}

    candidates = [
        info.get("listTime"),
        market.get("created")
        if isinstance(market, dict)
        else None,
    ]

    for candidate in candidates:
        value = safe_float(candidate, 0.0)

        if value <= 0:
            continue

        if value < 10_000_000_000:
            value *= 1000

        return int(value)

    return 0


def market_is_supported(market):
    if not isinstance(market, dict):
        return False

    info = market.get("info") or {}

    if market.get("active") is False:
        return False

    if not bool(market.get("swap")):
        return False

    if market.get("linear") is False:
        return False

    if str(market.get("quote") or "").upper() != "USDT":
        return False

    settle = str(
        market.get("settle") or ""
    ).upper()

    if settle and settle != "USDT":
        return False

    state = str(
        info.get("state") or ""
    ).lower()

    if state and state != "live":
        return False

    rule_type = str(
        info.get("ruleType") or "normal"
    ).lower()

    if rule_type in {
        "pre_market",
        "rebase_contract",
    }:
        return False

    return True


def discover_new_markets(
    exchange,
    current_ms=None,
):
    if current_ms is None:
        current_ms = now_ms()

    exchange.load_markets(reload=True)

    results = []

    for symbol, market in exchange.markets.items():
        if not market_is_supported(market):
            continue

        listing_ms = market_list_time_ms(market)

        if listing_ms <= 0:
            continue

        age_minutes = (
            current_ms - listing_ms
        ) / 60_000.0

        if age_minutes < MIN_LISTING_AGE_MINUTES:
            continue

        if age_minutes > (
            MAX_LISTING_AGE_HOURS * 60
        ):
            continue

        results.append({
            "symbol": symbol,
            "display_symbol": normalize_display_symbol(
                symbol
            ),
            "listing_ms": listing_ms,
            "listing_ts": int(
                listing_ms / 1000
            ),
            "age_minutes": round(
                age_minutes,
                2,
            ),
            "market": market,
        })

    results.sort(
        key=lambda item: item["listing_ms"],
        reverse=True,
    )

    return results[
        :MAX_NEW_MARKETS_PER_RUN
    ]


def ticker_quote_volume(ticker):
    quote_volume = safe_float(
        ticker.get("quoteVolume"),
        0.0,
    )

    if quote_volume > 0:
        return quote_volume

    last_price = safe_float(
        ticker.get("last"),
        0.0,
    )
    base_volume = safe_float(
        ticker.get("baseVolume"),
        0.0,
    )

    if last_price > 0 and base_volume > 0:
        return last_price * base_volume

    info = ticker.get("info") or {}

    for key in (
        "volCcy24h",
        "volUsd24h",
        "quoteVolume",
    ):
        value = safe_float(
            info.get(key),
            0.0,
        )

        if value > 0:
            return value

    return 0.0


def completed_candles(
    candles,
    timeframe_ms=60_000,
    current_ms=None,
):
    if current_ms is None:
        current_ms = now_ms()

    cleaned = [
        candle
        for candle in candles
        if isinstance(candle, (list, tuple))
        and len(candle) >= 6
    ]

    if not cleaned:
        return []

    last_timestamp = int(
        safe_float(
            cleaned[-1][0],
            0,
        )
    )

    if (
        last_timestamp > 0
        and current_ms
        < last_timestamp + timeframe_ms
    ):
        cleaned = cleaned[:-1]

    return cleaned


def price_n_minutes_ago(
    candles,
    minutes,
):
    if not candles:
        return None

    index = len(candles) - int(minutes)

    if index < 0:
        index = 0

    return safe_float(
        candles[index][4],
        0.0,
    )


def recent_volume_ratio(
    candles,
    recent_count,
    baseline_count=20,
):
    if len(candles) < recent_count + 5:
        return 0.0

    recent = candles[-recent_count:]
    baseline_end = len(candles) - recent_count
    baseline_start = max(
        0,
        baseline_end - baseline_count,
    )
    baseline = candles[
        baseline_start:baseline_end
    ]

    if not baseline:
        return 0.0

    recent_total = sum(
        safe_float(candle[5], 0.0)
        for candle in recent
    )
    baseline_average = average([
        safe_float(candle[5], 0.0)
        for candle in baseline
    ])

    expected_total = (
        baseline_average * recent_count
    )

    if expected_total <= 0:
        return 0.0

    return recent_total / expected_total


def average_true_range_percent(
    candles,
    window=14,
):
    if not candles:
        return 0.0

    closed = [
        candle
        for candle in candles
        if isinstance(candle, (list, tuple))
        and len(candle) >= 5
    ]

    if len(closed) < 2:
        return 0.0

    true_ranges = []

    for index in range(1, len(closed)):
        high = safe_float(
            closed[index][2],
            0.0,
        )
        low = safe_float(
            closed[index][3],
            0.0,
        )
        previous_close = safe_float(
            closed[index - 1][4],
            0.0,
        )

        if high <= 0 or low <= 0 or previous_close <= 0:
            continue

        true_ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )

    if not true_ranges:
        return 0.0

    current_close = safe_float(
        closed[-1][4],
        0.0,
    )

    if current_close <= 0:
        return 0.0

    selected = true_ranges[-int(window):]

    return (
        average(selected)
        / current_close
    ) * 100.0


def analyze_market_snapshot(
    market_info,
    ticker,
    candles,
):
    closed = completed_candles(candles)

    if len(closed) < MIN_CLOSED_1M_CANDLES:
        return None

    current_price = safe_float(
        ticker.get("last"),
        0.0,
    )

    if current_price <= 0:
        current_price = safe_float(
            closed[-1][4],
            0.0,
        )

    if current_price <= 0:
        return None

    highs = [
        safe_float(candle[2], 0.0)
        for candle in closed
    ]
    lows = [
        safe_float(candle[3], 0.0)
        for candle in closed
        if safe_float(candle[3], 0.0) > 0
    ]

    listing_high = max(highs) if highs else current_price
    listing_low = min(lows) if lows else current_price

    recent_15 = closed[-15:]
    recent_high_15 = max(
        safe_float(candle[2], 0.0)
        for candle in recent_15
    )
    recent_low_15 = min(
        safe_float(candle[3], current_price)
        for candle in recent_15
    )

    moves = {}

    for minutes in (3, 5, 15, 30, 60):
        reference_price = price_n_minutes_ago(
            closed,
            minutes,
        )
        moves[minutes] = percentage_change(
            current_price,
            reference_price,
        )

    drawdown_from_high = percentage_change(
        current_price,
        listing_high,
    )
    bounce_from_low = percentage_change(
        current_price,
        listing_low,
    )
    recent_bounce = percentage_change(
        current_price,
        recent_low_15,
    )
    recent_high_distance = percentage_change(
        current_price,
        recent_high_15,
    )

    last_candle = closed[-1]
    close_power = candle_close_power(
        last_candle
    )

    quote_volume = ticker_quote_volume(
        ticker
    )

    return {
        "symbol": market_info["symbol"],
        "display_symbol": market_info[
            "display_symbol"
        ],
        "listing_ts": market_info[
            "listing_ts"
        ],
        "listing_age_minutes": market_info[
            "age_minutes"
        ],
        "current_price": current_price,
        "quote_volume_24h": quote_volume,
        "move_3m": moves[3],
        "move_5m": moves[5],
        "move_15m": moves[15],
        "move_30m": moves[30],
        "move_60m": moves[60],
        "volume_ratio_1m": (
            recent_volume_ratio(
                closed,
                1,
            )
        ),
        "volume_ratio_3m": (
            recent_volume_ratio(
                closed,
                3,
            )
        ),
        "volume_ratio_5m": (
            recent_volume_ratio(
                closed,
                5,
            )
        ),
        "close_power": close_power,
        "listing_high": listing_high,
        "listing_low": listing_low,
        "drawdown_from_high": (
            drawdown_from_high
        ),
        "bounce_from_low": (
            bounce_from_low
        ),
        "recent_high_15": recent_high_15,
        "recent_low_15": recent_low_15,
        "recent_bounce": recent_bounce,
        "recent_high_distance": (
            recent_high_distance
        ),
        "atr_1m_percent": (
            average_true_range_percent(
                closed,
                window=14,
            )
        ),
        "candle_count": len(closed),
    }


def add_score(
    score,
    condition,
    points,
):
    if condition:
        return score + points

    return score


def build_candidates(snapshot):
    if not snapshot:
        return []

    move_3m = safe_float(
        snapshot.get("move_3m"),
        0.0,
    )
    move_5m = safe_float(
        snapshot.get("move_5m"),
        0.0,
    )
    move_15m = safe_float(
        snapshot.get("move_15m"),
        0.0,
    )
    move_30m = safe_float(
        snapshot.get("move_30m"),
        0.0,
    )

    volume_1m = safe_float(
        snapshot.get("volume_ratio_1m"),
        0.0,
    )
    volume_3m = safe_float(
        snapshot.get("volume_ratio_3m"),
        0.0,
    )
    volume_5m = safe_float(
        snapshot.get("volume_ratio_5m"),
        0.0,
    )

    close_power = safe_float(
        snapshot.get("close_power"),
        50.0,
    )
    drawdown = safe_float(
        snapshot.get("drawdown_from_high"),
        0.0,
    )
    bounce = safe_float(
        snapshot.get("bounce_from_low"),
        0.0,
    )
    recent_bounce = safe_float(
        snapshot.get("recent_bounce"),
        0.0,
    )
    high_distance = safe_float(
        snapshot.get("recent_high_distance"),
        0.0,
    )

    candidates = []

    # 1) Yeni liste yükseliş atağı
    long_attack_score = 0
    long_attack_score = add_score(
        long_attack_score,
        move_5m >= 0.80,
        22,
    )
    long_attack_score = add_score(
        long_attack_score,
        move_15m >= 1.50,
        20,
    )
    long_attack_score = add_score(
        long_attack_score,
        volume_5m >= 1.30,
        20,
    )
    long_attack_score = add_score(
        long_attack_score,
        volume_1m >= 1.25,
        12,
    )
    long_attack_score = add_score(
        long_attack_score,
        close_power >= 58,
        12,
    )
    long_attack_score = add_score(
        long_attack_score,
        high_distance >= -2.50,
        8,
    )
    long_attack_score = add_score(
        long_attack_score,
        move_15m <= 8.0,
        6,
    )

    if (
        move_5m >= 0.80
        and move_15m >= 1.50
        and volume_5m >= 1.30
        and close_power >= 55
        and move_15m <= 10.0
    ):
        candidates.append({
            "alert_type": "YENI_LISTE_ATAK_LONG",
            "title": "Yeni Liste Yükseliş Atağı",
            "direction": "LONG",
            "score": long_attack_score,
            "reason": (
                "Kısa vadeli yükseliş ve hacim "
                "aynı yönde hızlanıyor."
            ),
        })

    # 2) Yeni liste düşüş atağı
    short_attack_score = 0
    short_attack_score = add_score(
        short_attack_score,
        move_5m <= -0.80,
        22,
    )
    short_attack_score = add_score(
        short_attack_score,
        move_15m <= -1.50,
        20,
    )
    short_attack_score = add_score(
        short_attack_score,
        volume_5m >= 1.30,
        20,
    )
    short_attack_score = add_score(
        short_attack_score,
        volume_1m >= 1.25,
        12,
    )
    short_attack_score = add_score(
        short_attack_score,
        close_power <= 42,
        12,
    )
    short_attack_score = add_score(
        short_attack_score,
        recent_bounce <= 2.50,
        8,
    )
    short_attack_score = add_score(
        short_attack_score,
        move_15m >= -8.0,
        6,
    )

    if (
        move_5m <= -0.80
        and move_15m <= -1.50
        and volume_5m >= 1.30
        and close_power <= 45
        and move_15m >= -10.0
    ):
        candidates.append({
            "alert_type": "YENI_LISTE_ATAK_SHORT",
            "title": "Yeni Liste Düşüş Atağı",
            "direction": "SHORT",
            "score": short_attack_score,
            "reason": (
                "Kısa vadeli düşüş ve hacim "
                "aynı yönde hızlanıyor."
            ),
        })

    # 3) Pump sonrası ilk kontrollü geri çekilmeden toparlanma
    pullback_long_score = 0
    pullback_long_score = add_score(
        pullback_long_score,
        -10.0 <= drawdown <= -2.0,
        22,
    )
    pullback_long_score = add_score(
        pullback_long_score,
        move_3m >= 0.45,
        20,
    )
    pullback_long_score = add_score(
        pullback_long_score,
        move_5m >= 0.20,
        14,
    )
    pullback_long_score = add_score(
        pullback_long_score,
        volume_3m >= 1.10,
        18,
    )
    pullback_long_score = add_score(
        pullback_long_score,
        close_power >= 58,
        12,
    )
    pullback_long_score = add_score(
        pullback_long_score,
        0.50 <= recent_bounce <= 5.0,
        8,
    )
    pullback_long_score = add_score(
        pullback_long_score,
        move_30m >= 1.0,
        6,
    )

    if (
        -10.0 <= drawdown <= -2.0
        and move_3m >= 0.45
        and move_5m >= 0.20
        and volume_3m >= 1.10
        and close_power >= 55
        and move_30m >= 0.50
    ):
        candidates.append({
            "alert_type": "ILK_GERI_CEKILME_LONG",
            "title": (
                "İlk Geri Çekilme Toparlanması"
            ),
            "direction": "LONG",
            "score": pullback_long_score,
            "reason": (
                "İlk yükseliş sonrası geri çekilme "
                "hacimli tepkiyle toparlanıyor."
            ),
        })

    # 4) Pump sonrası ilk red / aşağı tepki
    rejection_short_score = 0
    rejection_short_score = add_score(
        rejection_short_score,
        -7.0 <= drawdown <= -1.0,
        20,
    )
    rejection_short_score = add_score(
        rejection_short_score,
        move_3m <= -0.45,
        20,
    )
    rejection_short_score = add_score(
        rejection_short_score,
        move_5m <= -0.20,
        14,
    )
    rejection_short_score = add_score(
        rejection_short_score,
        volume_3m >= 1.10,
        18,
    )
    rejection_short_score = add_score(
        rejection_short_score,
        close_power <= 42,
        12,
    )
    rejection_short_score = add_score(
        rejection_short_score,
        move_30m >= 2.0,
        10,
    )
    rejection_short_score = add_score(
        rejection_short_score,
        bounce >= 5.0,
        6,
    )

    if (
        -7.0 <= drawdown <= -1.0
        and move_3m <= -0.45
        and move_5m <= -0.20
        and volume_3m >= 1.10
        and close_power <= 45
        and move_30m >= 1.50
    ):
        candidates.append({
            "alert_type": "ILK_RED_SHORT",
            "title": "İlk Yükseliş Sonrası Red",
            "direction": "SHORT",
            "score": rejection_short_score,
            "reason": (
                "İlk pump sonrasında hacimli "
                "aşağı red oluşuyor."
            ),
        })

    # 5) Sert düşüş sonrası ilk tepki
    dip_reaction_score = 0
    dip_reaction_score = add_score(
        dip_reaction_score,
        move_30m <= -2.0,
        22,
    )
    dip_reaction_score = add_score(
        dip_reaction_score,
        move_3m >= 0.45,
        20,
    )
    dip_reaction_score = add_score(
        dip_reaction_score,
        move_5m >= 0.20,
        14,
    )
    dip_reaction_score = add_score(
        dip_reaction_score,
        volume_3m >= 1.10,
        18,
    )
    dip_reaction_score = add_score(
        dip_reaction_score,
        close_power >= 58,
        12,
    )
    dip_reaction_score = add_score(
        dip_reaction_score,
        1.0 <= recent_bounce <= 6.0,
        8,
    )
    dip_reaction_score = add_score(
        dip_reaction_score,
        bounce <= 10.0,
        6,
    )

    if (
        move_30m <= -2.0
        and move_3m >= 0.45
        and move_5m >= 0.20
        and volume_3m >= 1.10
        and close_power >= 55
        and 0.75 <= recent_bounce <= 7.0
    ):
        candidates.append({
            "alert_type": "DIP_TEPKI_LONG",
            "title": "Sert Düşüş Sonrası İlk Tepki",
            "direction": "LONG",
            "score": dip_reaction_score,
            "reason": (
                "Sert düşüş sonrası ilk hacimli "
                "yukarı tepki görülüyor."
            ),
        })

    for candidate in candidates:
        candidate["score"] = int(
            clamp(
                candidate["score"],
                0,
                100,
            )
        )

    return sorted(
        [
            candidate
            for candidate in candidates
            if candidate["score"]
            >= MIN_ALERT_SCORE
        ],
        key=lambda item: item["score"],
        reverse=True,
    )


def alert_key(
    display_symbol,
    alert_type,
):
    return (
        f"{display_symbol}|{alert_type}"
    )


def alert_is_blocked(
    state,
    display_symbol,
    alert_type,
    current_ts=None,
):
    if current_ts is None:
        current_ts = now_ts()

    exact_key = alert_key(
        display_symbol,
        alert_type,
    )

    last_exact = safe_float(
        state.get(
            "last_alerts",
            {},
        ).get(exact_key),
        0.0,
    )

    if (
        current_ts - last_exact
        < ALERT_DUPLICATE_SECONDS
    ):
        return True

    last_symbol = safe_float(
        state.get(
            "last_symbol_alerts",
            {},
        ).get(display_symbol),
        0.0,
    )

    if (
        current_ts - last_symbol
        < SYMBOL_ALERT_COOLDOWN_SECONDS
    ):
        return True

    return False


def mark_alert_sent(
    state,
    display_symbol,
    alert_type,
    sent_at,
):
    state.setdefault(
        "last_alerts",
        {},
    )[alert_key(
        display_symbol,
        alert_type,
    )] = sent_at

    state.setdefault(
        "last_symbol_alerts",
        {},
    )[display_symbol] = sent_at


def clean_state(state):
    current_time = now_ts()
    cutoff = current_time - (
        7 * 24 * 60 * 60
    )

    for container_name in (
        "last_alerts",
        "last_symbol_alerts",
        "trade_signal_cooldowns",
    ):
        container = state.get(
            container_name,
            {},
        )

        if not isinstance(container, dict):
            state[container_name] = {}
            continue

        state[container_name] = {
            key: value
            for key, value in container.items()
            if safe_float(value, 0) >= cutoff
        }

    silent_candidates = state.get(
        "silent_candidates",
        {},
    )

    if not isinstance(silent_candidates, dict):
        state["silent_candidates"] = {}
    else:
        state["silent_candidates"] = {
            key: value
            for key, value in silent_candidates.items()
            if isinstance(value, dict)
            and safe_float(
                value.get("expires_at"),
                0,
            )
            >= current_time - (6 * 60 * 60)
        }

    listing_cutoff = current_time - (
        14 * 24 * 60 * 60
    )

    seen = state.get(
        "seen_listings",
        {},
    )

    if not isinstance(seen, dict):
        state["seen_listings"] = {}
    else:
        state["seen_listings"] = {
            key: value
            for key, value in seen.items()
            if safe_float(
                (
                    value.get("listing_ts")
                    if isinstance(value, dict)
                    else 0
                ),
                0,
            )
            >= listing_cutoff
        }


def trade_signal_is_blocked(
    state,
    display_symbol,
    current_ts=None,
):
    if current_ts is None:
        current_ts = now_ts()

    last_sent = safe_float(
        state.get(
            "trade_signal_cooldowns",
            {},
        ).get(display_symbol),
        0.0,
    )

    return (
        current_ts - last_sent
        < TRADE_SIGNAL_COOLDOWN_SECONDS
    )


def mark_trade_signal_sent(
    state,
    display_symbol,
    sent_at,
):
    state.setdefault(
        "trade_signal_cooldowns",
        {},
    )[display_symbol] = sent_at


def create_or_update_silent_candidate(
    state,
    snapshot,
    candidate,
    current_ts=None,
):
    if current_ts is None:
        current_ts = now_ts()

    display_symbol = snapshot[
        "display_symbol"
    ]

    if trade_signal_is_blocked(
        state,
        display_symbol,
        current_ts,
    ):
        return "COOLDOWN"

    container = state.setdefault(
        "silent_candidates",
        {},
    )
    existing = container.get(
        display_symbol
    )

    direction = candidate["direction"]

    confirmation_level = (
        snapshot["recent_high_15"]
        if direction == "LONG"
        else snapshot["recent_low_15"]
    )
    invalidation_level = (
        snapshot["recent_low_15"]
        if direction == "LONG"
        else snapshot["recent_high_15"]
    )

    if isinstance(existing, dict):
        existing_direction = str(
            existing.get("direction") or ""
        ).upper()

        if (
            existing_direction != direction
            and candidate["score"]
            < safe_float(
                existing.get("best_score"),
                0,
            )
            + 5
        ):
            return "KEPT_EXISTING"

        if existing_direction == direction:
            existing["last_seen_at"] = (
                current_ts
            )
            existing["last_seen_at_tr"] = (
                iso_tr(current_ts)
            )
            existing["latest_score"] = (
                candidate["score"]
            )
            existing["best_score"] = max(
                safe_float(
                    existing.get("best_score"),
                    0,
                ),
                candidate["score"],
            )
            existing["latest_snapshot"] = (
                snapshot
            )
            existing["reason"] = candidate[
                "reason"
            ]
            existing["title"] = candidate[
                "title"
            ]

            # Onay seviyesi yükseldikçe hedefi sürekli uzağa taşımayalım.
            # İlk görülen yapı esas alınır.
            return "UPDATED"

    container[display_symbol] = {
        "symbol": snapshot["symbol"],
        "display_symbol": display_symbol,
        "direction": direction,
        "alert_type": candidate[
            "alert_type"
        ],
        "title": candidate["title"],
        "reason": candidate["reason"],
        "first_seen_at": current_ts,
        "first_seen_at_tr": iso_tr(
            current_ts
        ),
        "last_seen_at": current_ts,
        "last_seen_at_tr": iso_tr(
            current_ts
        ),
        "expires_at": (
            current_ts
            + SILENT_CANDIDATE_EXPIRY_MINUTES
            * 60
        ),
        "watch_price": snapshot[
            "current_price"
        ],
        "confirmation_level": (
            confirmation_level
        ),
        "invalidation_level": (
            invalidation_level
        ),
        "initial_score": candidate[
            "score"
        ],
        "latest_score": candidate[
            "score"
        ],
        "best_score": candidate[
            "score"
        ],
        "listing_ts": snapshot[
            "listing_ts"
        ],
        "listing_age_minutes": snapshot[
            "listing_age_minutes"
        ],
        "initial_snapshot": snapshot,
        "latest_snapshot": snapshot,
    }

    return (
        "REPLACED"
        if isinstance(existing, dict)
        else "CREATED"
    )


def silent_candidate_lifecycle(
    watch,
    snapshot,
    current_ts=None,
):
    if current_ts is None:
        current_ts = now_ts()

    if current_ts >= int(
        safe_float(
            watch.get("expires_at"),
            0,
        )
    ):
        return "EXPIRED"

    if not snapshot:
        return "WAITING"

    direction = str(
        watch.get("direction") or ""
    ).upper()
    current_price = safe_float(
        snapshot.get("current_price"),
        0.0,
    )
    invalidation_level = safe_float(
        watch.get("invalidation_level"),
        0.0,
    )

    if (
        direction == "LONG"
        and invalidation_level > 0
        and current_price
        < invalidation_level * 0.992
    ):
        return "INVALIDATED"

    if (
        direction == "SHORT"
        and invalidation_level > 0
        and current_price
        > invalidation_level * 1.008
    ):
        return "INVALIDATED"

    return "ACTIVE"


def evaluate_trade_confirmation(
    watch,
    snapshot,
    current_ts=None,
):
    if current_ts is None:
        current_ts = now_ts()

    if not isinstance(watch, dict):
        return None

    if not isinstance(snapshot, dict):
        return None

    first_seen_at = int(
        safe_float(
            watch.get("first_seen_at"),
            0,
        )
    )
    watch_age_minutes = (
        current_ts - first_seen_at
    ) / 60.0

    if (
        watch_age_minutes
        < MIN_CONFIRM_WATCH_AGE_MINUTES
    ):
        return None

    direction = str(
        watch.get("direction") or ""
    ).upper()
    current_price = safe_float(
        snapshot.get("current_price"),
        0.0,
    )
    watch_price = safe_float(
        watch.get("watch_price"),
        0.0,
    )
    confirmation_level = safe_float(
        watch.get("confirmation_level"),
        0.0,
    )

    if (
        direction not in {"LONG", "SHORT"}
        or current_price <= 0
        or watch_price <= 0
        or confirmation_level <= 0
    ):
        return None

    move_3m = safe_float(
        snapshot.get("move_3m"),
        0.0,
    )
    move_5m = safe_float(
        snapshot.get("move_5m"),
        0.0,
    )
    move_15m = safe_float(
        snapshot.get("move_15m"),
        0.0,
    )
    volume_1m = safe_float(
        snapshot.get("volume_ratio_1m"),
        0.0,
    )
    volume_3m = safe_float(
        snapshot.get("volume_ratio_3m"),
        0.0,
    )
    volume_5m = safe_float(
        snapshot.get("volume_ratio_5m"),
        0.0,
    )
    close_power = safe_float(
        snapshot.get("close_power"),
        50.0,
    )

    entry_drift = directional_return(
        direction,
        watch_price,
        current_price,
    )
    level_distance = directional_return(
        direction,
        confirmation_level,
        current_price,
    )

    if entry_drift is None or level_distance is None:
        return None

    volume_ok = (
        volume_1m >= MIN_CONFIRM_VOLUME_1M
        and volume_3m >= MIN_CONFIRM_VOLUME_3M
        and volume_5m >= MIN_CONFIRM_VOLUME_5M
    )

    not_late = (
        entry_drift
        <= MAX_CONFIRM_ENTRY_DRIFT_PERCENT
        and level_distance
        <= MAX_CONFIRM_LEVEL_DISTANCE_PERCENT
        and abs(move_5m)
        <= MAX_CONFIRM_5M_MOVE_PERCENT
    )

    if direction == "LONG":
        breakout_ok = (
            level_distance >= 0.10
        )
        retest_hold_ok = (
            -0.30 <= level_distance <= 0.40
            and move_3m >= 0.35
            and close_power >= 60
        )
        structure_ok = (
            breakout_ok
            or retest_hold_ok
        )
        momentum_ok = (
            move_3m >= 0.15
            and move_5m >= 0.20
            and move_15m >= -0.50
        )
        close_ok = close_power >= 58

    else:
        breakout_ok = (
            level_distance >= 0.10
        )
        retest_hold_ok = (
            -0.30 <= level_distance <= 0.40
            and move_3m <= -0.35
            and close_power <= 40
        )
        structure_ok = (
            breakout_ok
            or retest_hold_ok
        )
        momentum_ok = (
            move_3m <= -0.15
            and move_5m <= -0.20
            and move_15m <= 0.50
        )
        close_ok = close_power <= 42

    score = 0
    score = add_score(
        score,
        structure_ok,
        25,
    )
    score = add_score(
        score,
        volume_1m
        >= MIN_CONFIRM_VOLUME_1M,
        10,
    )
    score = add_score(
        score,
        volume_3m
        >= MIN_CONFIRM_VOLUME_3M,
        15,
    )
    score = add_score(
        score,
        volume_5m
        >= MIN_CONFIRM_VOLUME_5M,
        15,
    )
    score = add_score(
        score,
        momentum_ok,
        15,
    )
    score = add_score(
        score,
        close_ok,
        10,
    )
    score = add_score(
        score,
        safe_float(
            watch.get("best_score"),
            0,
        )
        >= 90,
        5,
    )
    score = add_score(
        score,
        not_late,
        5,
    )

    required_ok = (
        structure_ok
        and volume_ok
        and momentum_ok
        and close_ok
        and not_late
    )

    if (
        not required_ok
        or score < MIN_CONFIRM_SCORE
    ):
        return None

    return {
        "confirmation_score": int(
            clamp(
                score,
                0,
                100,
            )
        ),
        "watch_age_minutes": round(
            watch_age_minutes,
            2,
        ),
        "entry_drift_percent": round(
            entry_drift,
            4,
        ),
        "level_distance_percent": round(
            level_distance,
            4,
        ),
        "structure": (
            "KIRILIM"
            if breakout_ok
            else "RETEST_TUTUNMA"
        ),
        "reason": (
            "Kırılım/retest yapısı, hacim ve kısa "
            "vadeli momentum birlikte onaylandı."
        ),
    }


def build_trade_plan(
    watch,
    snapshot,
    confirmation,
):
    direction = str(
        watch.get("direction") or ""
    ).upper()
    entry = safe_float(
        snapshot.get("current_price"),
        0.0,
    )

    if entry <= 0:
        return None

    atr_percent = max(
        0.20,
        safe_float(
            snapshot.get("atr_1m_percent"),
            0.0,
        ),
    )

    atr_stop_percent = clamp(
        atr_percent * 2.20,
        MIN_STOP_PERCENT,
        MAX_STOP_PERCENT,
    )

    if direction == "LONG":
        support_values = [
            safe_float(
                snapshot.get("recent_low_15"),
                0.0,
            ),
            safe_float(
                watch.get("invalidation_level"),
                0.0,
            ),
        ]
        support_values = [
            value
            for value in support_values
            if 0 < value < entry
        ]

        technical_stop_percent = 0.0

        if support_values:
            nearest_support = max(
                support_values
            )
            support_distance = (
                (entry - nearest_support)
                / entry
            ) * 100.0

            if (
                0.35
                <= support_distance
                <= 2.60
            ):
                technical_stop_percent = (
                    support_distance + 0.25
                )

        stop_percent = clamp(
            max(
                atr_stop_percent,
                technical_stop_percent,
                MIN_STOP_PERCENT,
            ),
            MIN_STOP_PERCENT,
            MAX_STOP_PERCENT,
        )
        stop = entry * (
            1.0 - stop_percent / 100.0
        )
        risk = entry - stop
        tp1 = entry + (
            risk * TP1_R_MULTIPLIER
        )
        tp2 = entry + (
            risk * TP2_R_MULTIPLIER
        )
        tp3 = entry + (
            risk * TP3_R_MULTIPLIER
        )

    elif direction == "SHORT":
        resistance_values = [
            safe_float(
                snapshot.get("recent_high_15"),
                0.0,
            ),
            safe_float(
                watch.get("invalidation_level"),
                0.0,
            ),
        ]
        resistance_values = [
            value
            for value in resistance_values
            if value > entry
        ]

        technical_stop_percent = 0.0

        if resistance_values:
            nearest_resistance = min(
                resistance_values
            )
            resistance_distance = (
                (nearest_resistance - entry)
                / entry
            ) * 100.0

            if (
                0.35
                <= resistance_distance
                <= 2.60
            ):
                technical_stop_percent = (
                    resistance_distance + 0.25
                )

        stop_percent = clamp(
            max(
                atr_stop_percent,
                technical_stop_percent,
                MIN_STOP_PERCENT,
            ),
            MIN_STOP_PERCENT,
            MAX_STOP_PERCENT,
        )
        stop = entry * (
            1.0 + stop_percent / 100.0
        )
        risk = stop - entry
        tp1 = entry - (
            risk * TP1_R_MULTIPLIER
        )
        tp2 = entry - (
            risk * TP2_R_MULTIPLIER
        )
        tp3 = entry - (
            risk * TP3_R_MULTIPLIER
        )

    else:
        return None

    if risk <= 0:
        return None

    zone_half_percent = 0.20

    return {
        "direction": direction,
        "entry": entry,
        "entry_zone_low": entry * (
            1.0 - zone_half_percent / 100.0
        ),
        "entry_zone_high": entry * (
            1.0 + zone_half_percent / 100.0
        ),
        "sl": stop,
        "stop_percent": round(
            stop_percent,
            4,
        ),
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr_tp1": TP1_R_MULTIPLIER,
        "rr_tp2": TP2_R_MULTIPLIER,
        "rr_tp3": TP3_R_MULTIPLIER,
        "confirmation_score": (
            confirmation[
                "confirmation_score"
            ]
        ),
    }


def build_trade_message(
    watch,
    snapshot,
    confirmation,
    plan,
    portfolio_note="",
):
    message = (
        "✅ YENİ LİSTE GİRİŞ ONAYI\n\n"
        f"Coin: {snapshot['display_symbol']}\n"
        f"Yön: {plan['direction']}\n"
        f"Kurulum: {watch['title']}\n"
        f"Onay Skoru: "
        f"{confirmation['confirmation_score']}/100\n"
        f"Listelenme Yaşı: "
        f"{format_age_minutes(snapshot['listing_age_minutes'])}\n\n"
        f"🎯 Giriş Bölgesi: "
        f"{format_price(plan['entry_zone_low'])} – "
        f"{format_price(plan['entry_zone_high'])}\n"
        f"✅ TP1: {format_price(plan['tp1'])} "
        f"({plan['rr_tp1']:.1f}R)\n"
        f"✅ TP2: {format_price(plan['tp2'])} "
        f"({plan['rr_tp2']:.1f}R)\n"
        f"✅ TP3: {format_price(plan['tp3'])} "
        f"({plan['rr_tp3']:.1f}R)\n"
        f"🛑 SL: {format_price(plan['sl'])}\n"
        f"📏 Stop Mesafesi: "
        f"%{plan['stop_percent']:.2f}\n\n"
        f"📈 3M: "
        f"{format_signed_percent(snapshot['move_3m'])}\n"
        f"📈 5M: "
        f"{format_signed_percent(snapshot['move_5m'])}\n"
        f"📊 1M Hacim: "
        f"{snapshot['volume_ratio_1m']:.2f}x\n"
        f"📊 3M Hacim: "
        f"{snapshot['volume_ratio_3m']:.2f}x\n"
        f"📊 5M Hacim: "
        f"{snapshot['volume_ratio_5m']:.2f}x\n"
        f"🧭 Onay: {confirmation['reason']}\n"
        f"📐 İzleme Fiyatından Sapma: "
        f"{format_signed_percent(confirmation['entry_drift_percent'])}"
    )

    if portfolio_note:
        message += (
            f"\n{portfolio_note}"
        )

    message += (
        "\n\n⚠️ Yeni listelenen ürünlerde fitil, "
        "slippage ve tasfiye riski yüksektir.\n"
        "📌 Emir otomatik açılmaz. Isolated marjin, "
        "düşük kaldıraç ve manuel grafik kontrolü zorunludur."
    )

    return message


def add_trade_performance_record(
    ledger,
    watch,
    snapshot,
    confirmation,
    plan,
    sent_at,
):
    record_id = (
        f"{snapshot['display_symbol']}_"
        f"CONFIRMED_{plan['direction']}_"
        f"{sent_at}"
    )

    ledger.setdefault(
        "records",
        {},
    )[record_id] = {
        "record_id": record_id,
        "record_type": "CONFIRMED_TRADE",
        "status": "TRACKING",
        "symbol": snapshot["symbol"],
        "display_symbol": snapshot[
            "display_symbol"
        ],
        "alert_type": watch[
            "alert_type"
        ],
        "direction": plan[
            "direction"
        ],
        "score": confirmation[
            "confirmation_score"
        ],
        "alert_price": plan["entry"],
        "entry": plan["entry"],
        "entry_zone_low": plan[
            "entry_zone_low"
        ],
        "entry_zone_high": plan[
            "entry_zone_high"
        ],
        "sl": plan["sl"],
        "stop_percent": plan[
            "stop_percent"
        ],
        "tp1": plan["tp1"],
        "tp2": plan["tp2"],
        "tp3": plan["tp3"],
        "sent_at": sent_at,
        "sent_at_tr": iso_tr(sent_at),
        "listing_ts": snapshot[
            "listing_ts"
        ],
        "listing_age_minutes": snapshot[
            "listing_age_minutes"
        ],
        "silent_watch": {
            "first_seen_at": watch[
                "first_seen_at"
            ],
            "first_seen_at_tr": watch[
                "first_seen_at_tr"
            ],
            "watch_price": watch[
                "watch_price"
            ],
            "confirmation_level": watch[
                "confirmation_level"
            ],
            "watch_age_minutes": confirmation[
                "watch_age_minutes"
            ],
        },
        "snapshot": {
            "move_3m": snapshot[
                "move_3m"
            ],
            "move_5m": snapshot[
                "move_5m"
            ],
            "move_15m": snapshot[
                "move_15m"
            ],
            "move_30m": snapshot[
                "move_30m"
            ],
            "volume_ratio_1m": snapshot[
                "volume_ratio_1m"
            ],
            "volume_ratio_3m": snapshot[
                "volume_ratio_3m"
            ],
            "volume_ratio_5m": snapshot[
                "volume_ratio_5m"
            ],
            "atr_1m_percent": snapshot[
                "atr_1m_percent"
            ],
            "drawdown_from_high": snapshot[
                "drawdown_from_high"
            ],
            "recent_bounce": snapshot[
                "recent_bounce"
            ],
        },
        "checkpoints": {},
    }


def evaluate_portfolio_for_trade(
    snapshot,
    direction,
):
    if evaluate_portfolio_risk is None:
        return {
            "hard_block": False,
            "block_reason": None,
            "warnings": [],
        }

    try:
        return evaluate_portfolio_risk(
            symbol=snapshot[
                "display_symbol"
            ],
            direction=direction,
            source_bot="NEW_LISTING",
        )
    except Exception as exc:
        print(
            snapshot["display_symbol"],
            "portföy kontrol hatası:",
            exc,
        )
        return {
            "hard_block": False,
            "block_reason": None,
            "warnings": [],
        }


def build_alert_message(
    snapshot,
    candidate,
):
    direction_text = (
        "LONG İZLE"
        if candidate["direction"] == "LONG"
        else "SHORT İZLE"
    )

    return (
        "🆕 YENİ LİSTE FIRSAT RADARI\n\n"
        f"Coin: {snapshot['display_symbol']}\n"
        f"Yön: {direction_text}\n"
        f"Etiket: {candidate['title']}\n"
        f"Skor: {candidate['score']}/100\n"
        f"Listelenme Yaşı: "
        f"{format_age_minutes(snapshot['listing_age_minutes'])}\n\n"
        f"💰 Fiyat: "
        f"{format_price(snapshot['current_price'])}\n"
        f"📈 3M: "
        f"{format_signed_percent(snapshot['move_3m'])}\n"
        f"📈 5M: "
        f"{format_signed_percent(snapshot['move_5m'])}\n"
        f"📈 15M: "
        f"{format_signed_percent(snapshot['move_15m'])}\n"
        f"📈 30M: "
        f"{format_signed_percent(snapshot['move_30m'])}\n"
        f"📊 1M Hacim: "
        f"{snapshot['volume_ratio_1m']:.2f}x\n"
        f"📊 3M Hacim: "
        f"{snapshot['volume_ratio_3m']:.2f}x\n"
        f"📊 5M Hacim: "
        f"{snapshot['volume_ratio_5m']:.2f}x\n\n"
        f"🔺 Son 15M Direnç: "
        f"{format_price(snapshot['recent_high_15'])}\n"
        f"🔻 Son 15M Destek: "
        f"{format_price(snapshot['recent_low_15'])}\n"
        f"📐 İlk Yüksekten Uzaklık: "
        f"{format_signed_percent(snapshot['drawdown_from_high'])}\n"
        f"📐 Son Dipten Tepki: "
        f"{format_signed_percent(snapshot['recent_bounce'])}\n\n"
        f"🧭 Neden: {candidate['reason']}\n\n"
        "⚠️ Yeni listelenen coinler çok yüksek fitil, "
        "slippage ve tasfiye riski taşır.\n"
        "📌 Bu gerçek işlem sinyali değildir; "
        "grafiği açma ve fırsatı inceleme uyarısıdır."
    )


def add_performance_record(
    ledger,
    snapshot,
    candidate,
    sent_at,
):
    record_id = (
        f"{snapshot['display_symbol']}_"
        f"{candidate['alert_type']}_"
        f"{sent_at}"
    )

    ledger.setdefault(
        "records",
        {},
    )[record_id] = {
        "record_id": record_id,
        "status": "TRACKING",
        "symbol": snapshot["symbol"],
        "display_symbol": snapshot[
            "display_symbol"
        ],
        "alert_type": candidate[
            "alert_type"
        ],
        "direction": candidate[
            "direction"
        ],
        "score": candidate["score"],
        "alert_price": snapshot[
            "current_price"
        ],
        "sent_at": sent_at,
        "sent_at_tr": iso_tr(sent_at),
        "listing_ts": snapshot[
            "listing_ts"
        ],
        "listing_age_minutes": snapshot[
            "listing_age_minutes"
        ],
        "snapshot": {
            "move_3m": snapshot[
                "move_3m"
            ],
            "move_5m": snapshot[
                "move_5m"
            ],
            "move_15m": snapshot[
                "move_15m"
            ],
            "move_30m": snapshot[
                "move_30m"
            ],
            "volume_ratio_1m": snapshot[
                "volume_ratio_1m"
            ],
            "volume_ratio_3m": snapshot[
                "volume_ratio_3m"
            ],
            "volume_ratio_5m": snapshot[
                "volume_ratio_5m"
            ],
            "drawdown_from_high": snapshot[
                "drawdown_from_high"
            ],
            "recent_bounce": snapshot[
                "recent_bounce"
            ],
        },
        "checkpoints": {},
    }


def update_performance_tracking(
    exchange,
    ledger,
):
    records = ledger.get(
        "records",
        {},
    )

    if not isinstance(records, dict):
        return

    current_time = now_ts()
    ticker_cache = {}

    tracking_records = [
        record
        for record in records.values()
        if record.get("status") == "TRACKING"
    ]

    for record in tracking_records:
        sent_at = int(
            safe_float(
                record.get("sent_at"),
                0,
            )
        )

        if sent_at <= 0:
            continue

        elapsed_minutes = (
            current_time - sent_at
        ) / 60.0

        checkpoints = record.setdefault(
            "checkpoints",
            {},
        )

        due_windows = [
            window
            for window in PERFORMANCE_WINDOWS_MINUTES
            if elapsed_minutes >= window
            and str(window) not in checkpoints
        ]

        if not due_windows:
            continue

        symbol = str(
            record.get("symbol") or ""
        )

        if not symbol:
            continue

        if symbol not in ticker_cache:
            try:
                ticker_cache[symbol] = (
                    exchange.fetch_ticker(symbol)
                )
            except Exception as exc:
                print(
                    symbol,
                    "performans ticker hatası:",
                    exc,
                )
                ticker_cache[symbol] = None

        ticker = ticker_cache.get(symbol)

        if not ticker:
            continue

        current_price = safe_float(
            ticker.get("last"),
            0.0,
        )

        if current_price <= 0:
            continue

        for window in due_windows:
            return_percent = directional_return(
                record.get("direction"),
                record.get("alert_price"),
                current_price,
            )

            checkpoints[str(window)] = {
                "checked_at": current_time,
                "checked_at_tr": iso_tr(
                    current_time
                ),
                "price": current_price,
                "directional_return_percent": (
                    round(
                        safe_float(
                            return_percent,
                            0.0,
                        ),
                        4,
                    )
                ),
            }

        if (
            str(PERFORMANCE_FINAL_MINUTES)
            in checkpoints
        ):
            record["status"] = "FINAL"
            record["finalized_at"] = (
                current_time
            )
            record["finalized_at_tr"] = (
                iso_tr(current_time)
            )


def run_self_tests():
    assert (
        normalize_display_symbol(
            "FLY/USDT:USDT"
        )
        == "FLYUSDT"
    )

    assert round(
        directional_return(
            "LONG",
            100,
            102,
        ),
        2,
    ) == 2.0

    assert round(
        directional_return(
            "SHORT",
            100,
            98,
        ),
        2,
    ) == 2.0

    sample_candle = [
        0,
        100,
        110,
        90,
        108,
        1000,
    ]

    assert round(
        candle_close_power(
            sample_candle
        ),
        2,
    ) == 90.0

    synthetic_snapshot = {
        "symbol": "FWDI/USDT:USDT",
        "display_symbol": "FWDIUSDT",
        "listing_ts": now_ts() - 3600,
        "listing_age_minutes": 60,
        "current_price": 4.10,
        "move_3m": 0.70,
        "move_5m": 1.10,
        "move_15m": 2.50,
        "move_30m": 3.00,
        "volume_ratio_1m": 1.50,
        "volume_ratio_3m": 1.60,
        "volume_ratio_5m": 1.70,
        "close_power": 70,
        "drawdown_from_high": -3.0,
        "bounce_from_low": 5.0,
        "recent_bounce": 1.5,
        "recent_high_distance": -0.5,
        "recent_high_15": 4.14,
        "recent_low_15": 4.08,
        "atr_1m_percent": 0.35,
    }

    candidates = build_candidates(
        synthetic_snapshot
    )

    assert candidates

    state = empty_state()

    result = create_or_update_silent_candidate(
        state,
        synthetic_snapshot,
        candidates[0],
        current_ts=1_000_000,
    )

    assert result == "CREATED"
    assert "FWDIUSDT" in state[
        "silent_candidates"
    ]

    watch = state[
        "silent_candidates"
    ]["FWDIUSDT"]

    confirmation_snapshot = dict(
        synthetic_snapshot
    )
    confirmation_snapshot.update({
        "current_price": 4.15,
        "move_3m": 0.45,
        "move_5m": 0.55,
        "move_15m": 1.10,
        "volume_ratio_1m": 1.40,
        "volume_ratio_3m": 1.60,
        "volume_ratio_5m": 1.50,
        "close_power": 72,
        "recent_high_15": 4.15,
        "recent_low_15": 4.08,
    })

    confirmation = (
        evaluate_trade_confirmation(
            watch,
            confirmation_snapshot,
            current_ts=1_000_000
            + (5 * 60),
        )
    )

    assert confirmation is not None
    assert (
        confirmation[
            "confirmation_score"
        ]
        >= MIN_CONFIRM_SCORE
    )

    plan = build_trade_plan(
        watch,
        confirmation_snapshot,
        confirmation,
    )

    assert plan is not None
    assert plan["sl"] < plan["entry"]
    assert plan["tp1"] > plan["entry"]
    assert plan["tp3"] > plan["tp2"]

    print(
        "Yeni Liste Radar v2 self-test: BAŞARILI"
    )


def main():
    if "--self-test" in sys.argv:
        run_self_tests()
        return

    state = load_state()
    performance = load_performance()

    clean_state(state)

    state["stats"]["runs"] = (
        int(
            safe_float(
                state["stats"].get("runs"),
                0,
            )
        )
        + 1
    )
    state["last_run_at"] = iso_tr()

    exchange = build_exchange()

    try:
        new_markets = discover_new_markets(
            exchange
        )
    except Exception as exc:
        print(
            "Yeni liste market tarama hatası:",
            exc,
        )
        save_state(state)
        save_performance(performance)
        raise

    print(
        "Son 72 saatteki uygun yeni market:",
        len(new_markets),
    )

    state["stats"]["markets_seen"] = (
        int(
            safe_float(
                state["stats"].get(
                    "markets_seen"
                ),
                0,
            )
        )
        + len(new_markets)
    )

    current_time = now_ts()
    snapshots = {}
    current_candidates = {}

    # Tüm yeni marketler bir kez analiz edilir.
    for market_info in new_markets:
        display_symbol = market_info[
            "display_symbol"
        ]

        state.setdefault(
            "seen_listings",
            {},
        )[display_symbol] = {
            "symbol": market_info[
                "symbol"
            ],
            "listing_ts": market_info[
                "listing_ts"
            ],
            "listing_time_tr": iso_tr(
                market_info["listing_ts"]
            ),
            "last_seen_at": current_time,
            "last_seen_at_tr": iso_tr(
                current_time
            ),
        }

        try:
            ticker = exchange.fetch_ticker(
                market_info["symbol"]
            )

            quote_volume = (
                ticker_quote_volume(ticker)
            )

            if (
                quote_volume
                < MIN_24H_QUOTE_VOLUME
            ):
                print(
                    display_symbol,
                    "hacim düşük:",
                    round(
                        quote_volume,
                        2,
                    ),
                )
                continue

            candles = exchange.fetch_ohlcv(
                market_info["symbol"],
                timeframe="1m",
                limit=OHLCV_LIMIT,
            )

            snapshot = (
                analyze_market_snapshot(
                    market_info,
                    ticker,
                    candles,
                )
            )

            if not snapshot:
                print(
                    display_symbol,
                    "yeterli 1M veri yok.",
                )
                continue

            snapshots[
                display_symbol
            ] = snapshot

            candidates = build_candidates(
                snapshot
            )

            if candidates:
                current_candidates[
                    display_symbol
                ] = candidates[0]
            else:
                print(
                    display_symbol,
                    "sessiz aday şartı oluşmadı.",
                )

        except Exception as exc:
            print(
                display_symbol,
                "analiz hatası:",
                exc,
            )
            continue

    # Önce önceki çalışmalardan gelen sessiz adaylar onaylanır.
    confirmed_items = []
    silent_container = state.setdefault(
        "silent_candidates",
        {},
    )

    for display_symbol in list(
        silent_container.keys()
    ):
        watch = silent_container.get(
            display_symbol
        )
        snapshot = snapshots.get(
            display_symbol
        )

        lifecycle = silent_candidate_lifecycle(
            watch,
            snapshot,
            current_ts=current_time,
        )

        if lifecycle == "EXPIRED":
            silent_container.pop(
                display_symbol,
                None,
            )
            state["stats"][
                "candidate_expired"
            ] += 1
            print(
                display_symbol,
                "sessiz aday süresi doldu.",
            )
            continue

        if lifecycle == "INVALIDATED":
            silent_container.pop(
                display_symbol,
                None,
            )
            state["stats"][
                "candidate_invalidated"
            ] += 1
            print(
                display_symbol,
                "sessiz aday yapısı bozuldu.",
            )
            continue

        if lifecycle != "ACTIVE":
            continue

        if trade_signal_is_blocked(
            state,
            display_symbol,
            current_time,
        ):
            silent_container.pop(
                display_symbol,
                None,
            )
            continue

        confirmation = (
            evaluate_trade_confirmation(
                watch,
                snapshot,
                current_ts=current_time,
            )
        )

        if not confirmation:
            continue

        plan = build_trade_plan(
            watch,
            snapshot,
            confirmation,
        )

        if not plan:
            continue

        portfolio_result = (
            evaluate_portfolio_for_trade(
                snapshot,
                plan["direction"],
            )
        )

        if portfolio_result.get(
            "hard_block",
            False,
        ):
            state["stats"][
                "portfolio_blocks"
            ] += 1
            print(
                display_symbol,
                "portföy çakışması nedeniyle "
                "giriş onayı gönderilmedi:",
                portfolio_result.get(
                    "block_reason"
                ),
            )
            continue

        portfolio_note = ""

        if (
            format_portfolio_note
            is not None
        ):
            try:
                portfolio_note = (
                    format_portfolio_note(
                        portfolio_result
                    )
                )
            except Exception:
                portfolio_note = ""

        confirmed_items.append({
            "display_symbol": display_symbol,
            "watch": watch,
            "snapshot": snapshot,
            "confirmation": confirmation,
            "plan": plan,
            "portfolio_note": portfolio_note,
        })

    confirmed_items.sort(
        key=lambda item: (
            item["confirmation"][
                "confirmation_score"
            ],
            item["snapshot"][
                "volume_ratio_5m"
            ],
        ),
        reverse=True,
    )

    selected = confirmed_items[
        :MAX_TRADE_SIGNALS_PER_RUN
    ]

    print(
        "Gönderilecek yeni liste giriş onayı:",
        len(selected),
    )

    sent_symbols = set()

    for item in selected:
        message = build_trade_message(
            item["watch"],
            item["snapshot"],
            item["confirmation"],
            item["plan"],
            portfolio_note=item[
                "portfolio_note"
            ],
        )

        if send_telegram(message):
            sent_at = now_ts()
            display_symbol = item[
                "display_symbol"
            ]

            mark_trade_signal_sent(
                state,
                display_symbol,
                sent_at,
            )

            state["stats"][
                "trade_signals_sent"
            ] += 1
            state["stats"][
                "alerts_sent"
            ] += 1

            add_trade_performance_record(
                performance,
                item["watch"],
                item["snapshot"],
                item["confirmation"],
                item["plan"],
                sent_at,
            )

            silent_container.pop(
                display_symbol,
                None,
            )
            sent_symbols.add(
                display_symbol
            )

        else:
            state["stats"][
                "telegram_failures"
            ] += 1

    # Yeni fırsatlar yalnız state'e kaydedilir; Telegram mesajı gönderilmez.
    for display_symbol, candidate in (
        current_candidates.items()
    ):
        if display_symbol in sent_symbols:
            continue

        snapshot = snapshots.get(
            display_symbol
        )

        if not snapshot:
            continue

        result = (
            create_or_update_silent_candidate(
                state,
                snapshot,
                candidate,
                current_ts=current_time,
            )
        )

        if result in {
            "CREATED",
            "REPLACED",
        }:
            state["stats"][
                "candidate_created"
            ] += 1
            print(
                display_symbol,
                "sessiz takibe alındı:",
                candidate["direction"],
                candidate["title"],
            )

        elif result == "UPDATED":
            state["stats"][
                "candidate_updated"
            ] += 1

    try:
        update_performance_tracking(
            exchange,
            performance,
        )
    except Exception as exc:
        print(
            "Performans takip hatası:",
            exc,
        )

    save_state(state)
    save_performance(performance)

    print(
        "Sessiz takipte aday:",
        len(
            state.get(
                "silent_candidates",
                {},
            )
        ),
    )
    print(
        BOT_NAME,
        "tamamlandı.",
    )


if __name__ == "__main__":
    main()
