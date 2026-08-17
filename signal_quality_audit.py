import argparse
import json
import math
import os
import time
from collections import Counter, defaultdict


VERSION = "SIGNAL_QUALITY_AUDIT_V1_2026_08_17"
DEFAULT_LEDGER = "trade_ledger.json"
DEFAULT_POST_V2 = "post_result_shadow_v2_report.json"
DEFAULT_POST_V3 = "post_result_shadow_v3_report.json"
DEFAULT_OUTPUT = "signal_quality_audit.json"
RECENT_DAYS = 14

CLOSED_RESULTS = {
    "TP3",
    "TP2_SONRASI_BE",
    "TP1_SONRASI_BE",
    "SL",
    "EXPIRED",
}

POSITIVE_RESULTS = {
    "TP3",
    "TP2_SONRASI_BE",
    "TP1_SONRASI_BE",
}

TARGET_R_LEVELS = (1.60, 2.00, 2.50, 3.00, 4.00)


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


def load_json(path, default=None):
    if default is None:
        default = {}
    try:
        if not path or not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def percent(part, total):
    if not total:
        return 0.0
    return round(float(part) / float(total) * 100.0, 2)


def trade_result(trade):
    return str(trade.get("final_result") or "").upper()


def is_closed(trade):
    return (
        str(trade.get("status") or "").upper() == "CLOSED"
        and trade_result(trade) in CLOSED_RESULTS
    )


def closed_records(ledger):
    trades = ledger.get("trades") or {}
    if not isinstance(trades, dict):
        return []
    return [
        trade
        for trade in trades.values()
        if isinstance(trade, dict) and is_closed(trade)
    ]


def recent_records(records, current_ts=None, days=RECENT_DAYS):
    current_ts = int(current_ts or time.time())
    cutoff = current_ts - int(days * 86400)
    return [
        trade
        for trade in records
        if safe_int(trade.get("closed_at"), 0) >= cutoff
    ]


def outcome_stats(records):
    counts = Counter()
    r_values = []
    positive = 0
    negative = 0
    neutral = 0

    for trade in records:
        result = trade_result(trade)
        counts[result] += 1

        r_value = safe_float(trade.get("r_result"))
        if r_value is not None:
            r_values.append(r_value)
            if r_value > 0:
                positive += 1
            elif r_value < 0:
                negative += 1
            else:
                neutral += 1
        elif result in POSITIVE_RESULTS:
            positive += 1
        elif result == "SL":
            negative += 1
        else:
            neutral += 1

    measurable = sum(counts.values())
    net_r = sum(r_values)

    return {
        "sample": measurable,
        "outcomes": dict(sorted(counts.items())),
        "tp3_rate_percent": percent(counts.get("TP3", 0), measurable),
        "stop_rate_percent": percent(counts.get("SL", 0), measurable),
        "positive_close_rate_percent": percent(positive, measurable),
        "negative_close_rate_percent": percent(negative, measurable),
        "neutral_close_rate_percent": percent(neutral, measurable),
        "r_records": len(r_values),
        "net_r": round(net_r, 4),
        "avg_r": round(net_r / len(r_values), 4) if r_values else None,
    }


def root_cause_stats(records):
    primary = Counter()
    preliminary = Counter()
    finalized = 0
    provisional = 0
    returned_to_target = 0
    no_tp1_return = 0
    missing = 0

    for trade in records:
        if trade_result(trade) != "SL":
            continue

        follow = trade.get("post_stop_follow") or {}
        follow_status = str(follow.get("status") or "").upper()
        if follow_status == "RETURNED_TO_TARGET":
            returned_to_target += 1
        elif follow_status == "NO_TP1_RETURN":
            no_tp1_return += 1

        cause = trade.get("stop_root_cause")
        if not isinstance(cause, dict):
            missing += 1
            continue

        if bool(cause.get("provisional")):
            provisional += 1
            key = str(cause.get("preliminary") or "UNKNOWN").upper()
            preliminary[key] += 1
        else:
            finalized += 1
            key = str(cause.get("primary") or "UNKNOWN").upper()
            primary[key] += 1

    fitil_timing_count = (
        primary.get("FITIL_DAR_STOP", 0)
        + primary.get("ERKEN_GIRIS_VEYA_DAR_STOP", 0)
        + primary.get("MUHTEMEL_ERKEN_GIRIS", 0)
    )
    wrong_direction_count = primary.get("MUHTEMEL_YANLIS_YON", 0)
    late_entry_count = primary.get("GEC_UZAK_GIRIS", 0)

    return {
        "finalized": finalized,
        "provisional": provisional,
        "missing": missing,
        "primary_counts": dict(sorted(primary.items())),
        "preliminary_counts": dict(sorted(preliminary.items())),
        "post_stop_returned_to_target": returned_to_target,
        "post_stop_no_tp1_return": no_tp1_return,
        "fitil_or_timing_count": fitil_timing_count,
        "fitil_or_timing_share_percent": percent(fitil_timing_count, finalized),
        "wrong_direction_count": wrong_direction_count,
        "wrong_direction_share_percent": percent(wrong_direction_count, finalized),
        "late_entry_count": late_entry_count,
        "late_entry_share_percent": percent(late_entry_count, finalized),
    }


def entry_distance_bucket(value):
    value = abs(safe_float(value, 999.0))
    if value <= 0.10:
        return "VERY_CLOSE_0_10"
    if value <= 0.25:
        return "GOOD_0_10_0_25"
    if value <= 0.35:
        return "LIMIT_0_25_0_35"
    return "FAR_OVER_0_35"


def tp1_progress_bucket(value):
    value = safe_float(value)
    if value is None:
        return "UNKNOWN"
    if value <= 0:
        return "BEFORE_ENTRY_OR_PULLBACK"
    if value <= 20:
        return "EARLY_PATH_0_20"
    if value <= 45:
        return "LATE_BUT_ALLOWED_20_45"
    return "TOO_LATE_OVER_45"


def grouped_stats(records, key_fn):
    buckets = defaultdict(list)
    for trade in records:
        key = key_fn(trade)
        if key:
            buckets[str(key)].append(trade)
    return {
        key: outcome_stats(items)
        for key, items in sorted(buckets.items())
    }


def timing_stats(records):
    distance_records = [
        trade
        for trade in records
        if safe_float(trade.get("entry_distance_at_send_percent")) is not None
    ]
    progress_records = [
        trade
        for trade in records
        if safe_float(trade.get("tp1_progress_at_send_percent")) is not None
    ]
    zone_records = [
        trade
        for trade in records
        if safe_float(trade.get("zone_distance_percent")) is not None
    ]

    return {
        "entry_distance_coverage_percent": percent(
            len(distance_records), len(records)
        ),
        "tp1_progress_coverage_percent": percent(
            len(progress_records), len(records)
        ),
        "zone_distance_coverage_percent": percent(
            len(zone_records), len(records)
        ),
        "by_entry_distance_at_send": grouped_stats(
            distance_records,
            lambda trade: entry_distance_bucket(
                trade.get("entry_distance_at_send_percent")
            ),
        ),
        "by_tp1_progress_at_send": grouped_stats(
            progress_records,
            lambda trade: tp1_progress_bucket(
                trade.get("tp1_progress_at_send_percent")
            ),
        ),
        "by_zone_distance": grouped_stats(
            zone_records,
            lambda trade: entry_distance_bucket(
                trade.get("zone_distance_percent")
            ),
        ),
    }


def target_r(trade, target_key):
    entry = safe_float(trade.get("entry"))
    sl = safe_float(trade.get("sl"))
    target = safe_float(trade.get(target_key))
    if entry is None or sl is None or target is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    return abs(target - entry) / risk


def observed_max_r_with_post_result(trade):
    base_mfe = max(
        0.0,
        safe_float(trade.get("best_favorable_r"), 0.0),
    )
    follow = trade.get("post_result_shadow")
    if not isinstance(follow, dict):
        return None
    if str(follow.get("status") or "").upper() != "COMPLETED":
        return None

    post_mfe = max(
        0.0,
        safe_float(follow.get("max_favorable_r"), 0.0),
    )
    result = trade_result(trade)

    if result == "TP3":
        tp3_r = target_r(trade, "tp3")
        if tp3_r is None:
            return None
        post_absolute = tp3_r + post_mfe
    elif result in {"TP1_SONRASI_BE", "TP2_SONRASI_BE"}:
        # Bu sonuçlarda post_result_shadow referansı giriş/BE fiyatıdır.
        post_absolute = post_mfe
    else:
        return None

    return max(base_mfe, post_absolute)


def target_capacity_stats(records):
    eligible = []
    tp3_records = []

    for trade in records:
        max_r = observed_max_r_with_post_result(trade)
        if max_r is None:
            continue
        item = {"trade": trade, "max_observed_r": max_r}
        eligible.append(item)
        if trade_result(trade) == "TP3":
            tp3_records.append(item)

    reaches = {}
    for level in TARGET_R_LEVELS:
        reached = sum(
            1
            for item in eligible
            if item["max_observed_r"] >= level
        )
        reaches[f"{level:.2f}R"] = {
            "reached": reached,
            "rate_percent": percent(reached, len(eligible)),
        }

    tp3_extensions = {}
    for extra in (0.50, 1.00, 1.50, 2.00):
        reached = 0
        for item in tp3_records:
            follow = item["trade"].get("post_result_shadow") or {}
            post_mfe = safe_float(follow.get("max_favorable_r"), 0.0)
            if post_mfe >= extra:
                reached += 1
        tp3_extensions[f"+{extra:.2f}R"] = {
            "reached": reached,
            "rate_percent": percent(reached, len(tp3_records)),
        }

    return {
        "sample": len(eligible),
        "tp3_sample": len(tp3_records),
        "population_note": (
            "Yalnız TP1/TP2 sonrası BE veya TP3 ile kapanmış ve "
            "240 dakikalık post-result gölge takibi tamamlanmış işlemler."
        ),
        "absolute_target_reach": reaches,
        "tp3_after_close_extension": tp3_extensions,
    }


def version_stats(records):
    return grouped_stats(
        records,
        lambda trade: " | ".join([
            str(trade.get("bot_version") or "UNKNOWN_BOT"),
            str(trade.get("strategy_version") or "UNKNOWN_STRATEGY"),
            str(trade.get("config_version") or "UNKNOWN_CONFIG"),
        ]),
    )


def build_flags(recent_root, target_capacity, post_v3):
    flags = []

    finalized = safe_int(recent_root.get("finalized"), 0)
    if finalized >= 10:
        timing_share = safe_float(
            recent_root.get("fitil_or_timing_share_percent"),
            0.0,
        )
        wrong_share = safe_float(
            recent_root.get("wrong_direction_share_percent"),
            0.0,
        )
        late_share = safe_float(
            recent_root.get("late_entry_share_percent"),
            0.0,
        )

        if timing_share >= 35.0:
            flags.append({
                "priority": 1,
                "code": "STOP_TIMING_SHADOW_PRIORITY",
                "status": "SHADOW_TEST",
                "reason": (
                    f"Kesinleşmiş stop kök nedenlerinin %{timing_share:.1f}'i "
                    "fitil/dar stop veya erken giriş profiline giriyor."
                ),
                "next_action": (
                    "Canlı stopu genişletmeden önce giriş onayı ve dinamik "
                    "stop alternatiflerini ayrı gölge modelde karşılaştır."
                ),
            })

        if wrong_share >= 25.0:
            flags.append({
                "priority": 2,
                "code": "DIRECTION_FILTER_REVIEW",
                "status": "REVIEW",
                "reason": (
                    f"Kesinleşmiş stop kök nedenlerinin %{wrong_share:.1f}'i "
                    "muhtemel yanlış yön."
                ),
                "next_action": (
                    "4H/1H yön onayı, market guard ve momentum özelliklerini "
                    "kazanan-kaybeden ayrımıyla gölgede tekrar karşılaştır."
                ),
            })

        if late_share >= 20.0:
            flags.append({
                "priority": 3,
                "code": "LATE_ENTRY_REVIEW",
                "status": "REVIEW",
                "reason": (
                    f"Kesinleşmiş stop kök nedenlerinin %{late_share:.1f}'i "
                    "geç/uzak giriş."
                ),
                "next_action": (
                    "Entry-distance ve TP1-progress kovalarını son dönem Net R "
                    "ile karşılaştır; canlı eşiği kanıt olmadan daraltma."
                ),
            })

    tp3_sample = safe_int(target_capacity.get("tp3_sample"), 0)
    extension = (
        target_capacity.get("tp3_after_close_extension", {})
        .get("+0.50R", {})
    )
    extension_rate = safe_float(extension.get("rate_percent"), 0.0)

    if tp3_sample >= 20 and extension_rate >= 50.0:
        flags.append({
            "priority": 2,
            "code": "TP3_EXTENSION_SHADOW_CANDIDATE",
            "status": "SHADOW_TEST",
            "reason": (
                f"TP3 sonrası tamamlanmış {tp3_sample} örneğin "
                f"%{extension_rate:.1f}'i en az +0.50R daha devam etti."
            ),
            "next_action": (
                "Mevcut TP1/TP2/TP3 yapısını bozmadan TP3 sonrası küçük "
                "runner ve 2R–4R uzatma hedeflerini gölgede ölçmeye devam et."
            ),
        })

    models = post_v3.get("models") if isinstance(post_v3, dict) else {}
    runner = (models or {}).get("TP3_RUNNER_TRAIL_0_5R") or {}
    if (
        safe_int(runner.get("sample"), 0) >= 20
        and safe_float(runner.get("net_incremental_r"), 0.0) > 0
        and safe_float(runner.get("negative_rate"), 100.0) == 0.0
    ):
        flags.append({
            "priority": 1,
            "code": "EXISTING_TP3_RUNNER_MODEL_POSITIVE",
            "status": "KEEP_SHADOW",
            "reason": (
                "Mevcut TP3 +0.5R runner gölge modeli pozitif ek R üretiyor "
                "ve kayıtlı örnekte negatif fark göstermiyor."
            ),
            "next_action": (
                "Örnek sayısını büyüt; yeterli ileri dönem doğrulaması olmadan "
                "canlı TP kuralını değiştirme."
            ),
        })

    return sorted(
        flags,
        key=lambda item: (item["priority"], item["code"]),
    )


def build_report(ledger, post_v2=None, post_v3=None, current_ts=None):
    current_ts = int(current_ts or time.time())
    all_closed = closed_records(ledger)
    recent = recent_records(
        all_closed,
        current_ts=current_ts,
        days=RECENT_DAYS,
    )

    all_root = root_cause_stats(all_closed)
    recent_root = root_cause_stats(recent)
    target_capacity = target_capacity_stats(all_closed)

    report = {
        "version": VERSION,
        "mode": "ANALYSIS_ONLY_NO_SIGNAL_CHANGE_NO_TP_SL_CHANGE_NO_ORDERS",
        "generated_at": current_ts,
        "auto_apply": False,
        "methodology": {
            "recent_window_days": RECENT_DAYS,
            "entry_distance_buckets_percent": [0.10, 0.25, 0.35],
            "tp1_progress_buckets_percent": [0, 20, 45],
            "target_r_levels": list(TARGET_R_LEVELS),
            "stop_cause_rule": (
                "Yalnız provisional=false stop_root_cause kayıtları kesin "
                "kök neden dağılımına girer."
            ),
            "target_capacity_rule": (
                "TP1/TP2-BE ve TP3 işlemlerindeki tamamlanmış "
                "post_result_shadow verileriyle maksimum gözlenen R "
                "hesaplanır; SL işlemleri hedef kapasitesi örneğine girmez."
            ),
        },
        "data_quality": {
            "ledger_closed_trades": len(all_closed),
            "recent_closed_trades": len(recent),
            "post_v2_available": bool(post_v2),
            "post_v3_available": bool(post_v3),
        },
        "all_history": {
            "outcomes": outcome_stats(all_closed),
            "stop_root_causes": all_root,
            "by_direction": grouped_stats(
                all_closed,
                lambda trade: str(
                    trade.get("direction") or "UNKNOWN"
                ).upper(),
            ),
            "by_source": grouped_stats(
                all_closed,
                lambda trade: str(
                    trade.get("source") or "UNKNOWN"
                ).upper(),
            ),
        },
        "recent_14d": {
            "outcomes": outcome_stats(recent),
            "stop_root_causes": recent_root,
            "timing": timing_stats(recent),
            "by_direction": grouped_stats(
                recent,
                lambda trade: str(
                    trade.get("direction") or "UNKNOWN"
                ).upper(),
            ),
            "by_source": grouped_stats(
                recent,
                lambda trade: str(
                    trade.get("source") or "UNKNOWN"
                ).upper(),
            ),
            "by_version": version_stats(recent),
        },
        "target_capacity": target_capacity,
        "existing_post_result_v2": post_v2 or {},
        "existing_post_result_v3": post_v3 or {},
    }

    report["decision_flags"] = build_flags(
        recent_root=recent_root,
        target_capacity=target_capacity,
        post_v3=post_v3 or {},
    )
    return report


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Premium sinyal yön/zamanlama/TP kapasitesi kalite denetimi"
        )
    )
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--post-v2", default=DEFAULT_POST_V2)
    parser.add_argument("--post-v3", default=DEFAULT_POST_V3)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main():
    args = parse_args()
    ledger = load_json(args.ledger, {"trades": {}})
    post_v2 = load_json(args.post_v2, {})
    post_v3 = load_json(args.post_v3, {})
    report = build_report(
        ledger,
        post_v2=post_v2,
        post_v3=post_v3,
    )
    write_json(args.output, report)

    recent = report["recent_14d"]["outcomes"]
    roots = report["recent_14d"]["stop_root_causes"]
    targets = report["target_capacity"]

    print(
        "Signal Quality Audit | recent:",
        recent["sample"],
        "| stop:",
        recent["stop_rate_percent"],
        "| finalized stop cause:",
        roots["finalized"],
        "| target sample:",
        targets["sample"],
        "| flags:",
        len(report["decision_flags"]),
    )


if __name__ == "__main__":
    main()
