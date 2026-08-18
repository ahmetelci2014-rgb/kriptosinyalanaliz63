import argparse
import json
import math
import time

VERSION = "GENERAL_SIGNAL_DIAGNOSIS_V1_2026_08_18"
DEFAULT_QUALITY = "signal_quality_audit.json"
DEFAULT_SEQUENCE = "entry_sequence_shadow.json"
DEFAULT_OUTPUT = "general_signal_diagnosis.json"
MIN_SEQUENCE_SAMPLE = 30


def safe_float(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pct(part, total):
    return round((float(part) / float(total) * 100.0), 2) if total else 0.0


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def best_timing_bucket(timing):
    buckets = (timing or {}).get("by_entry_distance_at_send") or {}
    eligible = []
    for name, stats in buckets.items():
        if not isinstance(stats, dict):
            continue
        sample = safe_int(stats.get("sample"))
        avg_r = stats.get("avg_r")
        if sample >= 10 and avg_r is not None:
            eligible.append((safe_float(avg_r, -999.0), sample, str(name)))
    if not eligible:
        return None
    eligible.sort(reverse=True)
    avg_r, sample, name = eligible[0]
    return {"bucket": name, "sample": sample, "avg_r": round(avg_r, 4)}


def build_report(quality, sequence, now_ts=None):
    now_ts = int(now_ts or time.time())
    recent = quality.get("recent_14d") or {}
    roots = recent.get("stop_root_causes") or {}
    timing = recent.get("timing") or {}
    primary = roots.get("primary_counts") or {}

    finalized = safe_int(roots.get("finalized"))
    early_tight = safe_int(roots.get("fitil_or_timing_count"))
    late = safe_int(roots.get("late_entry_count"))
    wrong = safe_int(roots.get("wrong_direction_count"))
    setup = (
        safe_int(primary.get("KURULUM_DEVAM_ETMEDI"))
        + safe_int(primary.get("ZAYIF_TREND_HACIM"))
        + safe_int(primary.get("ONCE_LEHE_SONRA_TERS"))
    )
    entry_total = early_tight + late

    entry_share = pct(entry_total, finalized)
    wrong_share = pct(wrong, finalized)
    setup_share = pct(setup, finalized)

    seq_summary = sequence.get("summary") or {}
    seq_sample = safe_int(seq_summary.get("reliable_resolved_sample"))
    pressure_share = safe_float(
        seq_summary.get("winner_pre_tp1_mae_over_0_50r_rate_percent")
    )
    fail_before_tp1_share = safe_float(
        seq_summary.get("failed_before_tp1_rate_percent")
    )

    if entry_share >= 30.0 and entry_share >= wrong_share * 1.5:
        historical_focus = "ENTRY_TIMING_FIRST"
        historical_reason = (
            f"Son dönem kesin stoplarda erken/fitil + geç giriş toplam %{entry_share:.1f}; "
            f"muhtemel yanlış yön yalnız %{wrong_share:.1f}."
        )
    elif wrong_share >= 25.0:
        historical_focus = "DIRECTION_FIRST"
        historical_reason = (
            f"Muhtemel yanlış yön payı %{wrong_share:.1f}; yön teyidi öncelikli inceleme."
        )
    elif setup_share >= 30.0:
        historical_focus = "SETUP_TREND_VOLUME_FIRST"
        historical_reason = (
            f"Kurulum devam etmeme / zayıf trend-hacim / lehe sonra ters toplam "
            f"%{setup_share:.1f}."
        )
    else:
        historical_focus = "BALANCED_REVIEW"
        historical_reason = "Tek hata sınıfı baskın değil."

    ready = seq_sample >= MIN_SEQUENCE_SAMPLE
    if not ready:
        final_decision = "VERI_TOPLA_SEQUENCE"
        next_action = (
            f"Geçmiş veri {historical_focus} yönünü işaret ediyor; ancak erken giriş ile "
            f"sonradan geri dönüşü kesin ayırmak için {MIN_SEQUENCE_SAMPLE} canlı sıra "
            "sonucu tamamlanana kadar canlı filtreyi otomatik değiştirme."
        )
    else:
        if pressure_share >= 25.0:
            final_decision = "EARLY_ENTRY_CONFIRM_REVIEW"
            next_action = (
                "Kazananlarda TP1 öncesi >0.50R ters baskı yüksek; yalnız giriş teyidini "
                "gölgede sıkılaştır, yön filtresini genelleme."
            )
        elif fail_before_tp1_share >= 35.0 and wrong_share >= 20.0:
            final_decision = "DIRECTION_CONFIRM_REVIEW"
            next_action = (
                "TP1 öncesi başarısızlık ve yanlış yön birlikte yüksek; 4H/1H yön teyidi "
                "ve market guard adaylarını holdout gölgede karşılaştır."
            )
        elif fail_before_tp1_share >= 35.0:
            final_decision = "SETUP_CONFIRM_REVIEW"
            next_action = (
                "TP1 öncesi başarısızlık yüksek ama yanlış yön baskın değil; hacim, ADX, "
                "momentum ve retest teyidini karşılaştır."
            )
        elif historical_focus == "ENTRY_TIMING_FIRST":
            final_decision = "ENTRY_TIMING_REVIEW"
            next_action = (
                "Geçmiş ve canlı sıra verisi giriş kalitesini önceliklendiriyor; tek küçük "
                "entry filtresi adayını holdout gölgede test et."
            )
        else:
            final_decision = "KORU_IZLE"
            next_action = "Belirgin ortak hata yok; mevcut canlı kuralları koru."

    recent_outcomes = recent.get("outcomes") or {}
    all_history = quality.get("all_history") or {}
    all_outcomes = all_history.get("outcomes") or {}

    return {
        "version": VERSION,
        "mode": "ANALYSIS_ONLY_NO_SIGNAL_CHANGE_NO_TP_SL_CHANGE_NO_ORDERS",
        "generated_at": now_ts,
        "auto_apply": False,
        "data": {
            "all_closed_trades": safe_int(all_outcomes.get("sample")),
            "recent_closed_trades": safe_int(recent_outcomes.get("sample")),
            "finalized_recent_stop_causes": finalized,
            "entry_sequence_reliable_sample": seq_sample,
            "minimum_entry_sequence_sample": MIN_SEQUENCE_SAMPLE,
        },
        "historical_diagnosis": {
            "entry_timing_stop_count": entry_total,
            "entry_timing_stop_share_percent": entry_share,
            "wrong_direction_stop_count": wrong,
            "wrong_direction_stop_share_percent": wrong_share,
            "setup_trend_volume_stop_count": setup,
            "setup_trend_volume_stop_share_percent": setup_share,
            "focus": historical_focus,
            "reason": historical_reason,
            "best_entry_distance_bucket": best_timing_bucket(timing),
        },
        "precise_live_sequence": {
            "reliable_resolved_sample": seq_sample,
            "winner_pre_tp1_mae_over_0_50r_rate_percent": pressure_share,
            "failed_before_tp1_rate_percent": fail_before_tp1_share,
            "classification_counts": seq_summary.get("classification_counts") or {},
            "decision": seq_summary.get("decision") or "VERI_TOPLA",
            "note": (
                "Bu katman yalnız TP1 öncesi yolu kullanır; TP1 sonrası geri dönüş erken "
                "giriş sayılmaz."
            ),
        },
        "decision_ready": ready,
        "decision": final_decision,
        "next_action": next_action,
        "guardrail": (
            "Genel teşhis motoru tek başına canlı filtre değiştiremez. Değişiklik ancak "
            "yeterli sıra örneği + holdout/gölge doğrulaması sonrası ayrı onayla yapılır."
        ),
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Geçmiş sinyal kalitesi ile canlı giriş sırasını birleştirir."
    )
    parser.add_argument("--quality", default=DEFAULT_QUALITY)
    parser.add_argument("--sequence", default=DEFAULT_SEQUENCE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main():
    args = parse_args()
    report = build_report(load_json(args.quality), load_json(args.sequence))
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(
        "GENERAL_SIGNAL_DIAGNOSIS",
        "decision=", report["decision"],
        "historical_focus=", report["historical_diagnosis"]["focus"],
        "sequence_sample=", report["data"]["entry_sequence_reliable_sample"],
        "auto_apply=false",
    )


if __name__ == "__main__":
    main()
