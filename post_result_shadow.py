# post_result_shadow.py
# Premium MTF - TP/BE sonrasi sessiz golge takip
#
# Amac:
# - TP1 sonrasi BE kapanan islemleri 15/30/60/120/240 dk izlemek
# - TP2 sonrasi BE kapanan islemleri 15/30/60/120/240 dk izlemek
# - TP3 kapanan islemleri 15/30/60/120/240 dk izlemek
#
# Bu dosya Telegram mesaji gondermez, sinyal uretmez ve mevcut
# TP/SL/BE kurallarini degistirmez. Yalniz trade_ledger.json'a veri yazar.

import json
import os
import tempfile
import time

import ccxt


LEDGER_FILE = "trade_ledger.json"
VERSION = "POST_RESULT_SHADOW_V1_2026_08_10"
CHECKPOINT_MINUTES = [15, 30, 60, 120, 240]
MAX_TRACK_MINUTES = 240
RESTORE_MAX_HOURS = 24
TIMEFRAME = "1m"
CANDLE_SECONDS = 60
FETCH_LIMIT = 300
TRACKED_FINAL_RESULTS = {
    "TP1_SONRASI_BE",
    "TP2_SONRASI_BE",
    "TP3",
}


def now_ts():
    return int(time.time())


def safe_float(value, default=None):
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        if number != number:
            return default
        return number
    except Exception:
        return default


def load_ledger():
    try:
        if not os.path.exists(LEDGER_FILE):
            return {"trades": {}, "last_update": 0}

        with open(LEDGER_FILE, "r", encoding="utf-8") as handle:
            content = handle.read().strip()

        if not content:
            return {"trades": {}, "last_update": 0}

        data = json.loads(content)
        if not isinstance(data, dict):
            return {"trades": {}, "last_update": 0}

        data.setdefault("trades", {})
        data.setdefault("last_update", 0)

        if not isinstance(data["trades"], dict):
            data["trades"] = {}

        return data

    except Exception as exc:
        print("Post-result ledger okuma hatasi:", exc)
        return {"trades": {}, "last_update": 0}


def save_ledger(ledger):
    absolute = os.path.abspath(LEDGER_FILE)
    directory = os.path.dirname(absolute) or "."
    temp_path = None

    try:
        ledger["last_update"] = now_ts()
        os.makedirs(directory, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".trade_ledger.post_result.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(
                ledger,
                handle,
                indent=2,
                ensure_ascii=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(temp_path, "r", encoding="utf-8") as verify:
            verified = json.load(verify)

        if not isinstance(verified, dict):
            raise ValueError("gecici ledger dogrulamasi basarisiz")

        os.replace(temp_path, absolute)
        temp_path = None
        return True

    except Exception as exc:
        print("Post-result ledger kaydetme hatasi:", exc)
        return False

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


def to_okx_symbol(symbol):
    base = str(symbol or "").upper()
    if base.endswith("USDT"):
        base = base[:-4]
    return f"{base}/USDT:USDT"


def fetch_candles(exchange, symbol, since_seconds):
    try:
        rows = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=TIMEFRAME,
            since=max(0, int(since_seconds)) * 1000,
            limit=FETCH_LIMIT,
        )

        return [
            {
                "time": int(row[0] / 1000),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            }
            for row in (rows or [])
        ]

    except Exception as exc:
        print(symbol, "post-result mum verisi hatasi:", exc)
        return []


def directional_percent(direction, reference_price, price):
    reference = safe_float(reference_price)
    value = safe_float(price)

    if reference is None or value is None or reference <= 0:
        return None

    result = (value - reference) / reference * 100.0
    if str(direction).upper() == "SHORT":
        result = -result

    return round(result, 4)


def directional_r(trade, reference_price, price):
    entry = safe_float(trade.get("entry"))
    sl = safe_float(trade.get("sl"))
    reference = safe_float(reference_price)
    value = safe_float(price)

    if (
        entry is None
        or sl is None
        or reference is None
        or value is None
    ):
        return None

    risk = abs(entry - sl)
    if risk <= 0:
        return None

    if str(trade.get("direction", "")).upper() == "LONG":
        return round((value - reference) / risk, 4)

    if str(trade.get("direction", "")).upper() == "SHORT":
        return round((reference - value) / risk, 4)

    return None


def watched_levels(trade, final_result):
    levels = {}

    if final_result == "TP1_SONRASI_BE":
        names = ("TP1", "TP2", "TP3")
    elif final_result == "TP2_SONRASI_BE":
        names = ("TP2", "TP3")
    else:
        names = ()

    for name in names:
        value = safe_float(trade.get(name.lower()))
        if value is not None:
            levels[name] = value

    return levels


def initialize_follow(trade, final_result, current_ts):
    closed_at = int(trade.get("closed_at") or 0)
    if closed_at <= 0:
        return None

    age_hours = max(0.0, (current_ts - closed_at) / 3600.0)
    if age_hours > RESTORE_MAX_HOURS:
        return None

    # Kapanisin oldugu 1M mumda kapanis oncesi fiyat hareketi bulunabilir.
    # Yanlis "BE sonrasi hedef" saymamak icin ilk TAM dakikadan baslar.
    measurement_start_at = ((closed_at // 60) + 1) * 60

    if final_result in {"TP1_SONRASI_BE", "TP2_SONRASI_BE"}:
        reference_price = safe_float(
            trade.get("exit_price"),
            safe_float(trade.get("entry")),
        )
        reference_label = "BE_ENTRY"
    else:
        reference_price = safe_float(
            trade.get("exit_price"),
            safe_float(trade.get("tp3")),
        )
        reference_label = "TP3"

    if reference_price is None or reference_price <= 0:
        return None

    levels = watched_levels(trade, final_result)

    return {
        "version": VERSION,
        "shadow_only": True,
        "status": "TRACKING",
        "final_result": final_result,
        "started_at": closed_at,
        "measurement_start_at": measurement_start_at,
        "reference_label": reference_label,
        "reference_price": reference_price,
        "timeframe": TIMEFRAME,
        "checkpoints": {},
        "watched_levels": levels,
        "reached_levels": {},
        "max_favorable_percent": 0.0,
        "max_adverse_percent": 0.0,
        "max_favorable_r": 0.0,
        "max_adverse_r": 0.0,
        "best_price": reference_price,
        "worst_price": reference_price,
        "last_checked_at": 0,
        "completed_at": 0,
    }


def level_hit(direction, high, low, level_price):
    if str(direction).upper() == "LONG":
        return high >= level_price
    if str(direction).upper() == "SHORT":
        return low <= level_price
    return False


def update_reached_levels(trade, follow, candles):
    changed = False
    direction = str(trade.get("direction", "")).upper()
    reached = follow.setdefault("reached_levels", {})

    for name, raw_price in (follow.get("watched_levels") or {}).items():
        if name in reached:
            continue

        level_price = safe_float(raw_price)
        if level_price is None:
            continue

        for candle in candles:
            high = float(candle["high"])
            low = float(candle["low"])

            if level_hit(direction, high, low, level_price):
                reached[name] = {
                    "first_reached_at": int(candle["time"]),
                    "minutes_after_close": int(
                        max(
                            0,
                            (int(candle["time"]) - int(follow["started_at"])) / 60,
                        )
                    ),
                    "level_price": level_price,
                }
                changed = True
                break

    return changed


def update_excursion(trade, follow, candles):
    if not candles:
        return False

    direction = str(trade.get("direction", "")).upper()
    reference = safe_float(follow.get("reference_price"))
    if reference is None or reference <= 0:
        return False

    if direction == "LONG":
        best_price = max(
            [reference] + [float(c["high"]) for c in candles]
        )
        worst_price = min(
            [reference] + [float(c["low"]) for c in candles]
        )
    elif direction == "SHORT":
        best_price = min(
            [reference] + [float(c["low"]) for c in candles]
        )
        worst_price = max(
            [reference] + [float(c["high"]) for c in candles]
        )
    else:
        return False

    favorable_pct = directional_percent(direction, reference, best_price)
    adverse_pct_raw = directional_percent(direction, reference, worst_price)
    favorable_r = directional_r(trade, reference, best_price)
    adverse_r_raw = directional_r(trade, reference, worst_price)

    follow["best_price"] = round(best_price, 12)
    follow["worst_price"] = round(worst_price, 12)
    follow["max_favorable_percent"] = round(
        max(0.0, safe_float(favorable_pct, 0.0)),
        4,
    )
    follow["max_adverse_percent"] = round(
        max(0.0, -safe_float(adverse_pct_raw, 0.0)),
        4,
    )
    follow["max_favorable_r"] = round(
        max(0.0, safe_float(favorable_r, 0.0)),
        4,
    )
    follow["max_adverse_r"] = round(
        max(0.0, -safe_float(adverse_r_raw, 0.0)),
        4,
    )
    return True


def update_checkpoints(trade, follow, candles, current_ts):
    changed = False
    started_at = int(follow["started_at"])
    reference = safe_float(follow.get("reference_price"))
    direction = str(trade.get("direction", "")).upper()
    checkpoints = follow.setdefault("checkpoints", {})
    age_minutes = max(0, int((current_ts - started_at) / 60))

    for checkpoint in CHECKPOINT_MINUTES:
        key = str(checkpoint)
        if key in checkpoints or age_minutes < checkpoint:
            continue

        target_at = started_at + checkpoint * 60
        eligible = [
            candle
            for candle in candles
            if int(candle["time"]) + CANDLE_SECONDS <= target_at
        ]

        if not eligible:
            continue

        checkpoint_candle = eligible[-1]
        close_price = float(checkpoint_candle["close"])

        if direction == "LONG":
            cp_best = max(
                [reference] + [float(c["high"]) for c in eligible]
            )
            cp_worst = min(
                [reference] + [float(c["low"]) for c in eligible]
            )
        else:
            cp_best = min(
                [reference] + [float(c["low"]) for c in eligible]
            )
            cp_worst = max(
                [reference] + [float(c["high"]) for c in eligible]
            )

        close_pct = directional_percent(direction, reference, close_price)
        best_pct = directional_percent(direction, reference, cp_best)
        worst_pct = directional_percent(direction, reference, cp_worst)

        checkpoints[key] = {
            "target_at": target_at,
            "candle_time": int(checkpoint_candle["time"]),
            "close_price": round(close_price, 12),
            "directional_return_percent": close_pct,
            "max_favorable_percent": round(
                max(0.0, safe_float(best_pct, 0.0)),
                4,
            ),
            "max_adverse_percent": round(
                max(0.0, -safe_float(worst_pct, 0.0)),
                4,
            ),
            "directional_r_from_reference": directional_r(
                trade,
                reference,
                close_price,
            ),
        }
        changed = True

    return changed


def process_trade(exchange, trade_id, trade, current_ts):
    final_result = str(trade.get("final_result") or "").upper()
    if final_result not in TRACKED_FINAL_RESULTS:
        return False, False

    closed_at = int(trade.get("closed_at") or 0)
    if closed_at <= 0:
        return False, False

    follow = trade.get("post_result_shadow")
    changed = False

    if not isinstance(follow, dict):
        follow = initialize_follow(trade, final_result, current_ts)
        if follow is None:
            return False, False
        trade["post_result_shadow"] = follow
        changed = True

    if str(follow.get("status", "")).upper() == "COMPLETED":
        return changed, False

    measurement_start_at = int(
        follow.get("measurement_start_at")
        or (((closed_at // 60) + 1) * 60)
    )

    track_end_at = closed_at + MAX_TRACK_MINUTES * 60
    effective_end = min(current_ts, track_end_at)

    candles = fetch_candles(
        exchange,
        trade.get("symbol"),
        since_seconds=max(0, measurement_start_at - 60),
    )

    relevant = [
        candle
        for candle in candles
        if (
            int(candle["time"]) >= measurement_start_at
            and int(candle["time"]) + CANDLE_SECONDS <= effective_end
        )
    ]

    if not relevant:
        return changed, True

    if update_excursion(trade, follow, relevant):
        changed = True

    if update_reached_levels(trade, follow, relevant):
        changed = True

    if update_checkpoints(trade, follow, relevant, current_ts):
        changed = True

    follow["last_checked_at"] = current_ts
    follow["last_price"] = round(float(relevant[-1]["close"]), 12)
    changed = True

    age_minutes = max(0, int((current_ts - closed_at) / 60))
    if (
        age_minutes >= MAX_TRACK_MINUTES
        and str(MAX_TRACK_MINUTES) in follow.get("checkpoints", {})
    ):
        follow["status"] = "COMPLETED"
        follow["completed_at"] = current_ts
        changed = True
    else:
        follow["status"] = "TRACKING"

    return changed, True


def main():
    ledger = load_ledger()
    trades = ledger.get("trades", {})

    if not trades:
        print("Post-result shadow: ledger bos, takip yok.")
        return

    exchange = get_exchange()
    current_ts = now_ts()
    changed = False
    active_count = 0
    initialized_count = 0

    for trade_id, trade in trades.items():
        try:
            had_follow = isinstance(
                trade.get("post_result_shadow"),
                dict,
            )

            trade_changed, active = process_trade(
                exchange,
                trade_id,
                trade,
                current_ts,
            )

            if trade_changed:
                changed = True
                if not had_follow and isinstance(
                    trade.get("post_result_shadow"),
                    dict,
                ):
                    initialized_count += 1

            if active:
                active_count += 1

        except Exception as exc:
            print(trade_id, "post-result shadow takip hatasi:", exc)

    if changed:
        if save_ledger(ledger):
            print(
                "Post-result shadow kaydedildi | yeni:",
                initialized_count,
                "| aktif:",
                active_count,
            )
    else:
        print("Post-result shadow: yeni degisiklik yok.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Bu golge takip ana botu asla durdurmamalidir.
        print("Post-result shadow genel hata:", exc)
