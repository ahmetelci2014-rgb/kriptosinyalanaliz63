import json
import os
import tempfile
import time
from collections import defaultdict

try:
    import ccxt
except ImportError:  # Unit test ortaminda opsiyonel.
    ccxt = None

LEDGER_FILE = "trade_ledger.json"
REPORT_FILE = "post_result_shadow_v3_report.json"
VERSION = "POST_RESULT_SHADOW_V3_2026_08_14"
TIMEFRAME = "1m"
FETCH_LIMIT = 300
MAX_TRACK_MINUTES = 240
MIN_SAMPLE = 20


def now_ts():
    return int(time.time())


def safe_float(value, default=None):
    try:
        number = float(value)
        return default if number != number else number
    except (TypeError, ValueError):
        return default


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def atomic_save(path, data):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=".post_result_v3.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return True
    except Exception as exc:
        print("Post-result V3 kaydetme hatasi:", exc)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def get_exchange():
    if ccxt is None:
        raise RuntimeError("ccxt kurulu degil")
    return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})


def okx_symbol(symbol):
    base = str(symbol or "").upper()
    if base.endswith("USDT"):
        base = base[:-4]
    return f"{base}/USDT:USDT"


def fetch_candles(exchange, symbol, since_ts):
    try:
        rows = exchange.fetch_ohlcv(
            okx_symbol(symbol), timeframe=TIMEFRAME,
            since=int(since_ts) * 1000, limit=FETCH_LIMIT,
        )
        return [
            {"time": int(r[0] / 1000), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])}
            for r in (rows or [])
        ]
    except Exception as exc:
        print(symbol, "V3 mum verisi hatasi:", exc)
        return []


def price_r(direction, reference, price, risk):
    raw = (float(price) - reference) / risk
    return raw if direction == "LONG" else -raw


def candle_r(direction, reference, risk, candle):
    if direction == "LONG":
        favorable = price_r(direction, reference, candle["high"], risk)
        adverse = price_r(direction, reference, candle["low"], risk)
    else:
        favorable = price_r(direction, reference, candle["low"], risk)
        adverse = price_r(direction, reference, candle["high"], risk)
    return favorable, adverse


def finish(model, value, reason, candle=None):
    return {
        "model": model,
        "incremental_r": round(float(value), 4),
        "exit_reason": reason,
        "exit_at": int(candle["time"]) if candle else 0,
        "shadow_only": True,
    }


def simulate_fixed_stop_target(model, candles, direction, reference, risk, stop_r, target_r):
    for candle in candles:
        favorable, adverse = candle_r(direction, reference, risk, candle)
        if adverse <= stop_r:
            return finish(model, stop_r, "SHADOW_STOP", candle)
        if favorable >= target_r:
            return finish(model, target_r, "SHADOW_TARGET", candle)
    if not candles:
        return finish(model, 0.0, "NO_DATA")
    value = price_r(direction, reference, candles[-1]["close"], risk)
    return finish(model, value, "TIME_END_240M", candles[-1])


def simulate_delayed_be(candles, direction, reference, risk, tp2_r, tp3_r):
    model = "TP1_DELAY_BE_UNTIL_TP2"
    stop_r = -1.0
    tp2_hit = False
    for candle in candles:
        favorable, adverse = candle_r(direction, reference, risk, candle)
        if adverse <= stop_r:
            return finish(model, stop_r, "SHADOW_STOP", candle)
        if not tp2_hit and favorable >= tp2_r:
            tp2_hit = True
            stop_r = 0.0
            if adverse <= stop_r:
                return finish(model, stop_r, "AMBIGUOUS_BE_FIRST", candle)
        if tp2_hit and favorable >= tp3_r:
            return finish(model, tp3_r, "SHADOW_TP3", candle)
    if not candles:
        return finish(model, 0.0, "NO_DATA")
    value = price_r(direction, reference, candles[-1]["close"], risk)
    return finish(model, value, "TIME_END_240M", candles[-1])


def simulate_runner(candles, direction, reference, risk, trail_r):
    model = f"TP3_RUNNER_TRAIL_{str(trail_r).replace('.', '_')}R"
    peak_r = 0.0
    trail_stop = 0.0
    for candle in candles:
        favorable, adverse = candle_r(direction, reference, risk, candle)
        if adverse <= trail_stop:
            return finish(model, trail_stop, "TRAIL_STOP", candle)
        peak_r = max(peak_r, favorable)
        trail_stop = max(0.0, peak_r - trail_r)
    if not candles:
        return finish(model, 0.0, "NO_DATA")
    value = price_r(direction, reference, candles[-1]["close"], risk)
    return finish(model, max(trail_stop, value), "TIME_END_240M", candles[-1])


def build_models(trade, candles):
    direction = str(trade.get("direction") or "").upper()
    entry = safe_float(trade.get("entry"))
    sl = safe_float(trade.get("sl"))
    result = str(trade.get("final_result") or "").upper()
    follow = trade.get("post_result_shadow") or {}
    reference = safe_float(follow.get("reference_price"))
    if direction not in {"LONG", "SHORT"} or None in {entry, sl, reference}:
        return {}
    risk = abs(entry - sl)
    if risk <= 0:
        return {}
    tp2 = safe_float(trade.get("tp2"))
    tp3 = safe_float(trade.get("tp3"))
    models = {}
    if result == "TP1_SONRASI_BE" and tp2 is not None and tp3 is not None:
        tp2_r = price_r(direction, reference, tp2, risk)
        tp3_r = price_r(direction, reference, tp3, risk)
        first = simulate_fixed_stop_target(
            "TP1_SOFT_BE_MINUS_0_25R", candles, direction,
            reference, risk, -0.25, tp3_r,
        )
        second = simulate_delayed_be(candles, direction, reference, risk, tp2_r, tp3_r)
        models[first["model"]] = first
        models[second["model"]] = second
    elif result == "TP2_SONRASI_BE" and tp3 is not None:
        tp3_r = price_r(direction, reference, tp3, risk)
        item = simulate_fixed_stop_target(
            "TP2_SOFT_BE_MINUS_0_25R", candles, direction,
            reference, risk, -0.25, tp3_r,
        )
        models[item["model"]] = item
    elif result == "TP3":
        for distance in (0.5, 1.0):
            item = simulate_runner(candles, direction, reference, risk, distance)
            models[item["model"]] = item
    return models


def enrich_ledger(ledger, exchange):
    changed = 0
    eligible = 0
    for trade in (ledger.get("trades") or {}).values():
        follow = trade.get("post_result_shadow")
        if not isinstance(follow, dict) or follow.get("status") != "COMPLETED":
            continue
        eligible += 1
        existing = trade.get("post_result_shadow_v3")
        if isinstance(existing, dict) and existing.get("version") == VERSION:
            continue
        start = int(follow.get("measurement_start_at") or 0)
        closed_at = int(trade.get("closed_at") or 0)
        if start <= 0 or closed_at <= 0:
            continue
        end = closed_at + MAX_TRACK_MINUTES * 60
        candles = [
            c for c in fetch_candles(exchange, trade.get("symbol"), start)
            if start <= c["time"] and c["time"] + 60 <= end
        ]
        models = build_models(trade, candles)
        if not models:
            continue
        trade["post_result_shadow_v3"] = {
            "version": VERSION, "shadow_only": True, "status": "COMPLETED",
            "candle_count": len(candles), "models": models,
            "generated_at": now_ts(),
        }
        changed += 1
    return changed, eligible


def build_report(ledger, generated_at=None):
    rows = defaultdict(list)
    trade_count = 0
    for trade in (ledger.get("trades") or {}).values():
        v3 = trade.get("post_result_shadow_v3")
        if not isinstance(v3, dict) or v3.get("version") != VERSION:
            continue
        trade_count += 1
        for name, model in (v3.get("models") or {}).items():
            value = safe_float(model.get("incremental_r"))
            if value is not None:
                rows[name].append(value)
    models = {}
    for name, values in sorted(rows.items()):
        sample = len(values)
        models[name] = {
            "sample": sample,
            "evidence_gate": "ENOUGH_SAMPLE" if sample >= MIN_SAMPLE else "OBSERVE_ONLY",
            "average_incremental_r": round(sum(values) / sample, 4) if sample else 0.0,
            "net_incremental_r": round(sum(values), 4),
            "positive_rate": round(sum(v > 0 for v in values) * 100 / sample, 2) if sample else 0.0,
            "zero_rate": round(sum(v == 0 for v in values) * 100 / sample, 2) if sample else 0.0,
            "negative_rate": round(sum(v < 0 for v in values) * 100 / sample, 2) if sample else 0.0,
        }
    return {
        "version": VERSION, "shadow_only": True,
        "changes_live_rules": False, "generated_at": int(generated_at or now_ts()),
        "minimum_sample": MIN_SAMPLE, "modeled_trades": trade_count,
        "baseline": {"name": "CURRENT_RULE", "incremental_r_after_close": 0.0},
        "models": models,
        "decision": {"status": "COMPARE_ONLY", "automatic_rule_change": False},
    }


def main():
    if not os.path.exists(LEDGER_FILE):
        print("Post-result V3: trade ledger bulunamadi.")
        return
    ledger = load_json(LEDGER_FILE)
    changed, eligible = enrich_ledger(ledger, get_exchange())
    if changed and not atomic_save(LEDGER_FILE, ledger):
        return
    report = build_report(ledger)
    if atomic_save(REPORT_FILE, report):
        print("Post-result Shadow V3 | uygun:", eligible, "| yeni model:", changed)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Post-result Shadow V3 genel hata:", exc)
