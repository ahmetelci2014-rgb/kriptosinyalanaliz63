import argparse
import json
import math
import os
import time
from collections import defaultdict


VERSION = "TP_CAPACITY_SHADOW_V1_2026_08_17"
TARGET_LEVELS_R = (1.60, 2.00, 2.50, 3.00, 4.00)
RECENT_DAYS = 14
CLOSED_RESULTS = {
    "TP3",
    "TP2_SONRASI_BE",
    "TP1_SONRASI_BE",
    "SL",
    "EXPIRED",
}


def safe_float(value, default=None):
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def percent(part, total):
    if not total:
        return 0.0
    return round(float(part) / float(total) * 100.0, 2)


def load_json(path, default=None):
    if default is None:
        default = {}
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def closed_trades(ledger):
    trades = ledger.get("trades") or {}
    if not isinstance(trades, dict):
        return []
    result = []
    for trade in trades.values():
        if not isinstance(trade, dict):
            continue
        if str(trade.get("status") or "").upper() != "CLOSED":
            continue
        final_result = str(trade.get("final_result") or "").upper()
        if final_result not in CLOSED_RESULTS:
            continue
        result.append(trade)
    return result


def recent_trades(records, current_ts=None, days=RECENT_DAYS):
    current_ts = int(current_ts or time.time())
    cutoff = current_ts - int(days * 86400)
    return [
        trade
        for trade in records
        if safe_int(trade.get("closed_at"), 0) >= cutoff
    ]


def target_r(trade, key):
    entry = safe_float(trade.get("entry"))
    sl = safe_float(trade.get("sl"))
    target = safe_float(trade.get(key))
    if entry is None or sl is None or target is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    return abs(target - entry) / risk


def pre_close_capacity(records):
    measured = []
    for trade in records:
        mfe = safe_float(trade.get("best_favorable_r"))
        if mfe is None:
            continue
        measured.append((trade, max(0.0, mfe)))

    reaches = {}
    for level in TARGET_LEVELS_R:
        count = sum(1 for _, mfe in measured if mfe >= level)
        reaches[f"{level:.2f}R"] = {
            "reached": count,
            "rate_percent": percent(count, len(measured)),
        }

    return {
        "sample": len(measured),
        "coverage_percent": percent(len(measured), len(records)),
        "reach": reaches,
        "note": (
            "İşlem kapanmadan önce ledger'a kaydedilmiş best_favorable_r kullanılır. "
            "Bu oran, mevcut yönetim altında hedef seviyesinin kapanıştan önce görülüp "
            "görülmediğini ölçer. TP3'te kapanan işlemler için TP3 sonrası devamı içermez."
        ),
    }


def post_close_supplement(records):
    measured = []
    tp3_measured = []

    for trade in records:
        follow = trade.get("post_result_shadow")
        if not isinstance(follow, dict):
            continue
        if str(follow.get("status") or "").upper() != "COMPLETED":
            continue

        final_result = str(trade.get("final_result") or "").upper()
        if final_result not in {
            "TP3",
            "TP2_SONRASI_BE",
            "TP1_SONRASI_BE",
        }:
            continue

        base_mfe = max(0.0, safe_float(trade.get("best_favorable_r"), 0.0))
        post_mfe = max(0.0, safe_float(follow.get("max_favorable_r"), 0.0))

        if final_result == "TP3":
            tp3_value = target_r(trade, "tp3")
            if tp3_value is None:
                continue
            observed_upper = max(base_mfe, tp3_value + post_mfe)
        else:
            # BE sonrası takip giriş/BE fiyatını referans alır.
            observed_upper = max(base_mfe, post_mfe)

        item = (trade, observed_upper, post_mfe)
        measured.append(item)
        if final_result == "TP3":
            tp3_measured.append(item)

    reaches = {}
    for level in TARGET_LEVELS_R:
        count = sum(1 for _, value, _ in measured if value >= level)
        reaches[f"{level:.2f}R"] = {
            "reached": count,
            "rate_percent": percent(count, len(measured)),
        }

    extensions = {}
    for extra in (0.50, 1.00, 1.50, 2.00):
        count = sum(1 for _, _, post_mfe in tp3_measured if post_mfe >= extra)
        extensions[f"+{extra:.2f}R"] = {
            "reached": count,
            "rate_percent": percent(count, len(tp3_measured)),
        }

    return {
        "sample": len(measured),
        "tp3_sample": len(tp3_measured),
        "upper_bound_reach": reaches,
        "tp3_extension": extensions,
        "note": (
            "Bu bölüm TP1/TP2-BE ve TP3 sonrası 240 dakikalık gölge takibi kullanır. "
            "Yeni bir canlı stratejinin kesin hit-rate'i değildir; daha uzun hedeflerin "
            "potansiyel üst sınırını gösterir. SL işlemleri bu post-close örneğe girmez."
        ),
    }


def grouped_pre_close(records, field):
    groups = defaultdict(list)
    for trade in records:
        key = str(trade.get(field) or "UNKNOWN").upper()
        groups[key].append(trade)
    return {
        key: pre_close_capacity(items)
        for key, items in sorted(groups.items())
    }


def build_report(ledger, post_v3=None, current_ts=None):
    current_ts = int(current_ts or time.time())
    all_closed = closed_trades(ledger)
    recent = recent_trades(all_closed, current_ts=current_ts)

    current_tp3_price_r = 1.60
    current_tp1_price_r = 0.55
    current_realized_tp3_r = round(
        0.50 * current_tp1_price_r + 0.50 * current_tp3_price_r,
        4,
    )

    post_v3 = post_v3 if isinstance(post_v3, dict) else {}
    runner = (post_v3.get("models") or {}).get("TP3_RUNNER_TRAIL_0_5R") or {}

    return {
        "version": VERSION,
        "mode": "SHADOW_ONLY_NO_LIVE_TP_SL_CHANGE_NO_ORDERS",
        "generated_at": current_ts,
        "auto_apply": False,
        "current_structure": {
            "tp1_r": current_tp1_price_r,
            "tp2_r": 1.05,
            "tp3_r": current_tp3_price_r,
            "tp1_partial_fraction": 0.50,
            "realized_r_if_tp1_then_tp3": current_realized_tp3_r,
            "note": (
                "Mevcut Net R hesabında TP1'de pozisyonun yarısı alınır; kalan yarı TP3'e "
                "ulaşırsa toplam yaklaşık 1.075R olur."
            ),
        },
        "all_history": {
            "closed": len(all_closed),
            "pre_close": pre_close_capacity(all_closed),
            "post_close_supplement": post_close_supplement(all_closed),
            "by_source_pre_close": grouped_pre_close(all_closed, "source"),
            "by_direction_pre_close": grouped_pre_close(all_closed, "direction"),
        },
        "recent_14d": {
            "closed": len(recent),
            "pre_close": pre_close_capacity(recent),
            "post_close_supplement": post_close_supplement(recent),
            "by_source_pre_close": grouped_pre_close(recent, "source"),
            "by_direction_pre_close": grouped_pre_close(recent, "direction"),
        },
        "existing_runner_evidence": {
            "model": "TP3_RUNNER_TRAIL_0_5R",
            "sample": safe_int(runner.get("sample"), 0),
            "net_incremental_r": safe_float(runner.get("net_incremental_r"), 0.0),
            "average_incremental_r": safe_float(runner.get("average_incremental_r"), 0.0),
            "positive_rate": safe_float(runner.get("positive_rate"), 0.0),
            "zero_rate": safe_float(runner.get("zero_rate"), 0.0),
            "negative_rate": safe_float(runner.get("negative_rate"), 0.0),
        },
        "decision": {
            "status": "MEASURE_ONLY",
            "next_live_change": "NONE_AUTOMATIC",
            "candidate_order": [
                "KEEP_CURRENT_TP1_TP2_TP3_BASELINE",
                "VALIDATE_TP3_SMALL_RUNNER",
                "COMPARE_2R_2_5R_3R_4R_CAPACITY",
                "ONLY_THEN_CONSIDER_LONG_TREND_MODE",
            ],
        },
    }


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default="trade_ledger.json")
    parser.add_argument("--post-v3", default="post_result_shadow_v3_report.json")
    parser.add_argument("--output", default="tp_capacity_shadow_report.json")
    return parser.parse_args()


def main():
    args = parse_args()
    ledger = load_json(args.ledger, {"trades": {}})
    post_v3 = load_json(args.post_v3, {})
    report = build_report(ledger, post_v3=post_v3)
    write_json(args.output, report)
    print(
        "TP Capacity Shadow | all:",
        report["all_history"]["closed"],
        "| recent:",
        report["recent_14d"]["closed"],
        "| recent MFE coverage:",
        report["recent_14d"]["pre_close"]["coverage_percent"],
    )


if __name__ == "__main__":
    main()
