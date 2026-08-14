import json
import os
import tempfile
import time
from collections import defaultdict

LEDGER_FILE = "trade_ledger.json"
REPORT_FILE = "post_result_shadow_v2_report.json"
VERSION = "POST_RESULT_SHADOW_V2_2026_08_14"
MIN_SAMPLE = 20
CHECKPOINTS = (15, 30, 60, 120, 240)


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
            prefix=".post_result_v2.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return True
    except Exception as exc:
        print("Post-result V2 rapor kaydetme hatasi:", exc)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def new_bucket():
    return {
        "sample": 0, "complete": 0, "tp2_recovery": 0,
        "tp3_recovery": 0, "extension_0_5r": 0, "extension_1r": 0,
        "adverse_0_5r": 0, "mfe_r_sum": 0.0, "mae_r_sum": 0.0,
        "checkpoint_positive": {str(x): 0 for x in CHECKPOINTS},
        "checkpoint_samples": {str(x): 0 for x in CHECKPOINTS},
    }


def add_trade(bucket, trade):
    shadow = trade["post_result_shadow"]
    bucket["sample"] += 1
    if shadow.get("status") == "COMPLETED":
        bucket["complete"] += 1
    reached = shadow.get("reached_levels") or {}
    bucket["tp2_recovery"] += int("TP2" in reached)
    bucket["tp3_recovery"] += int("TP3" in reached)
    mfe = max(0.0, safe_float(shadow.get("max_favorable_r"), 0.0))
    mae = max(0.0, safe_float(shadow.get("max_adverse_r"), 0.0))
    bucket["mfe_r_sum"] += mfe
    bucket["mae_r_sum"] += mae
    bucket["extension_0_5r"] += int(mfe >= 0.5)
    bucket["extension_1r"] += int(mfe >= 1.0)
    bucket["adverse_0_5r"] += int(mae >= 0.5)
    checkpoints = shadow.get("checkpoints") or {}
    for minute in CHECKPOINTS:
        key = str(minute)
        if key not in checkpoints:
            continue
        bucket["checkpoint_samples"][key] += 1
        value = safe_float(checkpoints[key].get("directional_r_from_reference"))
        bucket["checkpoint_positive"][key] += int(value is not None and value > 0)


def percent(count, sample):
    return round(count * 100.0 / sample, 2) if sample else 0.0


def finalize(bucket, result_name=None):
    sample = bucket["sample"]
    output = {
        "sample": sample,
        "complete": bucket["complete"],
        "evidence_gate": "ENOUGH_SAMPLE" if sample >= MIN_SAMPLE else "OBSERVE_ONLY",
        "average_mfe_r": round(bucket["mfe_r_sum"] / sample, 4) if sample else 0.0,
        "average_mae_r": round(bucket["mae_r_sum"] / sample, 4) if sample else 0.0,
        "extension_0_5r_rate": percent(bucket["extension_0_5r"], sample),
        "extension_1r_rate": percent(bucket["extension_1r"], sample),
        "adverse_0_5r_rate": percent(bucket["adverse_0_5r"], sample),
        "checkpoint_positive_rate": {},
    }
    for minute in CHECKPOINTS:
        key = str(minute)
        output["checkpoint_positive_rate"][key] = percent(
            bucket["checkpoint_positive"][key],
            bucket["checkpoint_samples"][key],
        )
    if result_name == "TP1_SONRASI_BE":
        output["tp2_recovery_rate"] = percent(bucket["tp2_recovery"], sample)
        output["tp3_recovery_rate"] = percent(bucket["tp3_recovery"], sample)
    elif result_name == "TP2_SONRASI_BE":
        output["tp3_recovery_rate"] = percent(bucket["tp3_recovery"], sample)
    return output


def build_report(ledger, generated_at=None):
    trades = ledger.get("trades") or {}
    eligible = []
    skipped_incomplete = 0
    for trade_id, trade in trades.items():
        shadow = trade.get("post_result_shadow")
        if not isinstance(shadow, dict):
            continue
        if shadow.get("status") != "COMPLETED":
            skipped_incomplete += 1
            continue
        eligible.append((trade_id, trade))

    overall = new_bucket()
    by_result = defaultdict(new_bucket)
    by_source = defaultdict(new_bucket)
    by_direction = defaultdict(new_bucket)
    for _, trade in eligible:
        result = str(trade.get("final_result") or "UNKNOWN").upper()
        source = str(trade.get("source") or "UNKNOWN").upper()
        direction = str(trade.get("direction") or "UNKNOWN").upper()
        for bucket in (overall, by_result[result], by_source[source], by_direction[direction]):
            add_trade(bucket, trade)

    report = {
        "version": VERSION,
        "shadow_only": True,
        "changes_live_rules": False,
        "generated_at": int(generated_at or time.time()),
        "minimum_sample": MIN_SAMPLE,
        "data_quality": {
            "ledger_trades": len(trades),
            "completed_post_result_samples": len(eligible),
            "tracking_samples_excluded": skipped_incomplete,
        },
        "overall": finalize(overall),
        "by_final_result": {key: finalize(value, key) for key, value in sorted(by_result.items())},
        "by_source": {key: finalize(value) for key, value in sorted(by_source.items())},
        "by_direction": {key: finalize(value) for key, value in sorted(by_direction.items())},
        "decision": {
            "status": "SHADOW_OBSERVATION_ONLY",
            "automatic_rule_change": False,
            "note": "Bu rapor olcer; sinyal, TP, SL veya BE kuralini degistirmez.",
        },
    }
    return report


def main():
    if not os.path.exists(LEDGER_FILE):
        print("Post-result V2: trade ledger bulunamadi.")
        return
    report = build_report(load_json(LEDGER_FILE))
    if atomic_save(REPORT_FILE, report):
        print("Post-result Shadow V2 raporu kaydedildi | tamamlanan:", report["data_quality"]["completed_post_result_samples"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Post-result Shadow V2 genel hata:", exc)
