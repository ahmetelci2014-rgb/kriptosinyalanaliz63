#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kripto Sinyal Sistemi - Reçete Motoru V2

V1 Karar Motoru "nerede sorun var?" sorusuna cevap verir.
Bu V2 modülü "hangi değişiklik adayı neden denenmeli?" sorusuna cevap verir.

GÜVENLİK:
- Hiçbir canlı Python/config/strategy dosyasını değiştirmez.
- Telegram mesajı göndermez.
- OKX'e bağlanmaz, emir açmaz.
- auto_apply = False.
- Bir öneriyi önce geçmiş veride TRAIN + zaman sıralı HOLDOUT bölümünde sınar.
- Son bölümde doğrulanmayan öneriyi CANLI_ADAY yapmaz.
- Kazanan işlemleri aşırı kesen filtreleri reddeder.
- Verisi az olan öneriler en fazla GOLGE_TEST olabilir.

Çıktı:
- prescription_report.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "PRESCRIPTION_ENGINE_V2_2026_08_10"
MODE = "ANALYSIS_AND_BACKTEST_ONLY_NO_AUTO_APPLY_NO_ORDERS_NO_TELEGRAM"
DEFAULT_OUTPUT = "prescription_report.json"

FILES = {
    "decision_report": "decision_report.json",
    "premium": "trade_ledger.json",
    "scalp": "scalp_performance_ledger.json",
    "swing": "swing_performance_ledger.json",
    "pump": "pump_performance_ledger.json",
    "portfolio": "portfolio_risk_outcomes.json",
    "range": "range_shadow.json",
    "momentum": "momentum_shadow.json",
}

# Bu motor parametreyi canlıya uygulamaz. Eşikler yalnız reçete kalitesini sınıflandırır.
RULES = {
    "split": {
        "train_ratio": 0.70,
        "min_total": 18,
        "min_train": 12,
        "min_holdout": 6,
    },
    "candidate": {
        "min_blocked_train": 3,
        "min_kept_train": 8,
        "min_blocked_holdout": 2,
        "min_kept_holdout": 4,
        "max_candidates_per_component": 5,
        "max_numeric_features": 30,
    },
    "shadow": {
        "min_total": 20,
        "min_exact_r_coverage": 0.50,
        "min_total_delta_r": 0.40,
        "min_holdout_delta_r": 0.10,
        "max_winner_loss_percent": 20.0,
        "min_blocked_losers": 3,
    },
    "live_candidate": {
        "min_total": 60,
        "min_holdout": 15,
        "min_exact_r_coverage": 0.80,
        "min_total_delta_r": 2.00,
        "min_holdout_delta_r": 0.50,
        "max_winner_loss_percent": 8.0,
        "min_blocked_losers": 6,
    },
}

FINAL_ALIASES = {
    "STOP": "SL",
    "STOPPED": "SL",
    "STOPLOSS": "SL",
    "STOP_LOSS": "SL",
    "BREAKEVEN": "BE",
    "BREAK_EVEN": "BE",
    "TP1_BE": "TP1_SONRASI_BE",
    "TP2_BE": "TP2_SONRASI_BE",
}

CLOSED_RESULTS = {
    "TP3",
    "TP2_SONRASI_BE",
    "TP1_SONRASI_BE",
    "BE",
    "SL",
    "EXPIRED",
    "TIMEOUT",
}

# Geleceği bilen / sonuç sonrası oluşan alanlar reçete üretiminde feature olarak KULLANILMAZ.
LEAKAGE_TOKENS = {
    "result", "outcome", "closed", "close_reason", "exit", "best_", "worst_",
    "latest_", "snapshot", "milestone", "post_stop", "post_result",
    "diagnosis", "direction_status", "direction_reason", "trade_", "tp1_hit",
    "tp2_hit", "tp3_hit", "last_market", "last_checked", "last_tracking",
    "finalized", "reached", "mfe", "mae", "favorable", "adverse",
}

# Giriş anında bilinmesi mantıklı sayısal alanlar. Dinamik eşik yalnız bu desenlere uygulanır.
SAFE_NUMERIC_PREFIXES = (
    "score", "risk_percent", "stop_percent", "rsi_", "adx_", "vol_",
    "volume_", "volume_ratio", "dist_", "entry_distance", "zone_drift",
    "ema_", "macd_", "candle_", "close_power", "upper_wick", "lower_wick",
    "ok_count", "total_conditions",
)

SAFE_CATEGORICAL_FIELDS = (
    "source",
    "timing_mode",
    "direction",
    "signal_quality",
)

PROXY_R = {
    "TP3": 1.075,
    "TP2_SONRASI_BE": 0.675,
    "TP1_SONRASI_BE": 0.275,
    "BE": 0.275,
    "SL": -1.0,
    "EXPIRED": 0.0,
    "TIMEOUT": 0.0,
}


def now_ts() -> int:
    return int(time.time())


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    value = safe_float(value, None)
    return int(value) if value is not None else default


def pct(n: Any, d: Any) -> Optional[float]:
    n = safe_float(n, None)
    d = safe_float(d, None)
    if n is None or d is None or d <= 0:
        return None
    return round(n / d * 100.0, 2)


def mean(values: Iterable[Any]) -> Optional[float]:
    clean = [safe_float(v, None) for v in values]
    clean = [v for v in clean if v is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 4)


def median(values: Iterable[Any]) -> Optional[float]:
    clean = [safe_float(v, None) for v in values]
    clean = [v for v in clean if v is not None]
    if not clean:
        return None
    return round(float(statistics.median(clean)), 4)


def normalize_outcome(value: Any) -> str:
    text = str(value or "").upper().strip()
    text = (
        text.replace("İ", "I")
        .replace("Ş", "S")
        .replace("Ğ", "G")
        .replace("Ü", "U")
        .replace("Ö", "O")
        .replace("Ç", "C")
        .replace("-", "_")
        .replace(" ", "_")
    )
    if text in FINAL_ALIASES:
        return FINAL_ALIASES[text]
    if "TP2" in text and "BE" in text:
        return "TP2_SONRASI_BE"
    if "TP1" in text and "BE" in text:
        return "TP1_SONRASI_BE"
    if text.startswith("TP3"):
        return "TP3"
    if text in {"SL", "STOP", "STOPPED"}:
        return "SL"
    if text in {"BE", "BREAKEVEN"}:
        return "BE"
    if "EXPIRE" in text:
        return "EXPIRED"
    return text


def load_json(path: Path) -> Tuple[Dict[str, Any], Optional[str]]:
    if not path.exists():
        return {}, "FILE_NOT_FOUND"
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}, "EMPTY_FILE"
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}, "ROOT_NOT_OBJECT"
        return data, None
    except Exception as exc:
        return {}, f"JSON_ERROR: {str(exc)[:180]}"


def save_json_atomically(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(temp_path, "r", encoding="utf-8") as verify:
            checked = json.load(verify)
        if not isinstance(checked, dict):
            raise ValueError("Reçete raporu object değil.")

        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def iter_records(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("records", "trades", "closed_positions"):
        value = data.get(key)
        if isinstance(value, dict):
            return [item for item in value.values() if isinstance(item, dict)]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def record_outcome(record: Dict[str, Any]) -> str:
    for key in ("final_result", "trade_outcome", "outcome", "result"):
        if record.get(key) not in (None, ""):
            return normalize_outcome(record.get(key))
    return ""


def record_timestamp(record: Dict[str, Any]) -> int:
    for key in (
        "trade_closed_at", "closed_at", "sent_at", "opened_at",
        "recorded_at", "created_at",
    ):
        value = safe_int(record.get(key), 0)
        if value > 0:
            return value
    return 0


def is_closed(record: Dict[str, Any]) -> bool:
    outcome = record_outcome(record)
    return outcome in CLOSED_RESULTS or bool(
        outcome and (
            safe_int(record.get("trade_closed_at"), 0) > 0
            or safe_int(record.get("closed_at"), 0) > 0
        )
    )


def exact_r(record: Dict[str, Any]) -> Optional[float]:
    for key in ("r_result", "trade_result_r", "net_r", "realized_r"):
        value = safe_float(record.get(key), None)
        if value is not None:
            return value
    return None


def record_r(record: Dict[str, Any]) -> Tuple[Optional[float], str]:
    exact = exact_r(record)
    if exact is not None:
        return exact, "EXACT"
    outcome = record_outcome(record)
    if outcome in PROXY_R:
        return PROXY_R[outcome], "PROXY"
    return None, "NONE"


def outcome_class(record: Dict[str, Any]) -> str:
    outcome = record_outcome(record)
    if outcome == "SL":
        return "LOSER"
    if outcome in {"TP3", "TP2_SONRASI_BE", "TP1_SONRASI_BE", "BE"}:
        return "WINNER"
    return "NEUTRAL"


def get_path(record: Dict[str, Any], path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def safe_numeric_feature_name(name: str) -> bool:
    lower = name.lower()
    if any(token in lower for token in LEAKAGE_TOKENS):
        return False
    return lower.startswith(SAFE_NUMERIC_PREFIXES)


def discover_numeric_features(records: Sequence[Dict[str, Any]]) -> List[str]:
    counter: Counter[str] = Counter()
    for record in records:
        for key, value in record.items():
            if safe_numeric_feature_name(str(key)) and safe_float(value, None) is not None:
                counter[str(key)] += 1
        features = record.get("features")
        if isinstance(features, dict):
            for key, value in features.items():
                path = f"features.{key}"
                if safe_numeric_feature_name(str(key)) and safe_float(value, None) is not None:
                    counter[path] += 1

    minimum_coverage = max(6, int(len(records) * 0.45))
    candidates = [
        key for key, count in counter.most_common()
        if count >= minimum_coverage
    ]
    return candidates[: RULES["candidate"]["max_numeric_features"]]


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("empty")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


def candidate_thresholds(values: Sequence[float]) -> List[float]:
    unique = sorted(set(round(v, 10) for v in values))
    if len(unique) < 4:
        return []
    thresholds = []
    for q in (0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
        thresholds.append(round(percentile(unique, q), 10))
    return sorted(set(thresholds))


def chronological_split(records: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ordered = sorted(records, key=lambda r: (record_timestamp(r), str(r.get("id") or r.get("trade_id") or "")))
    n = len(ordered)
    if n < RULES["split"]["min_total"]:
        return ordered, []

    split_index = int(n * RULES["split"]["train_ratio"])
    split_index = max(RULES["split"]["min_train"], split_index)
    split_index = min(split_index, n - RULES["split"]["min_holdout"])
    if split_index <= 0 or split_index >= n:
        return ordered, []
    return ordered[:split_index], ordered[split_index:]


def summarize_side(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    r_values: List[float] = []
    exact_count = 0
    proxy_count = 0
    winners = losers = neutral = 0
    outcomes: Counter[str] = Counter()

    for record in records:
        value, source = record_r(record)
        if value is not None:
            r_values.append(value)
        if source == "EXACT":
            exact_count += 1
        elif source == "PROXY":
            proxy_count += 1

        cls = outcome_class(record)
        winners += int(cls == "WINNER")
        losers += int(cls == "LOSER")
        neutral += int(cls == "NEUTRAL")
        outcome = record_outcome(record)
        if outcome:
            outcomes[outcome] += 1

    return {
        "records": len(records),
        "r_records": len(r_values),
        "exact_r_records": exact_count,
        "proxy_r_records": proxy_count,
        "exact_r_coverage_percent": pct(exact_count, len(records)),
        "net_r": round(sum(r_values), 4) if r_values else None,
        "avg_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
        "winners": winners,
        "losers": losers,
        "neutral": neutral,
        "stop_rate_percent": pct(losers, winners + losers),
        "outcomes": dict(outcomes),
    }


def predicate_match(record: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    kind = candidate["kind"]
    field = candidate["field"]
    value = get_path(record, field)

    if kind == "CATEGORY_EQ":
        return str(value or "") == str(candidate["value"])

    number = safe_float(value, None)
    if number is None:
        return False

    threshold = float(candidate["value"])
    if kind == "NUM_LT":
        return number < threshold
    if kind == "NUM_GT":
        return number > threshold
    return False


def apply_candidate(records: Sequence[Dict[str, Any]], candidate: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    blocked = []
    kept = []
    for record in records:
        if predicate_match(record, candidate):
            blocked.append(record)
        else:
            kept.append(record)
    return blocked, kept


def evaluate_candidate_on(records: Sequence[Dict[str, Any]], candidate: Dict[str, Any]) -> Dict[str, Any]:
    blocked, kept = apply_candidate(records, candidate)
    base = summarize_side(records)
    blocked_stats = summarize_side(blocked)
    kept_stats = summarize_side(kept)

    base_r = safe_float(base.get("net_r"), None)
    kept_r = safe_float(kept_stats.get("net_r"), None)
    delta_r = None
    if base_r is not None and kept_r is not None:
        delta_r = round(kept_r - base_r, 4)

    return {
        "records": len(records),
        "blocked": blocked_stats,
        "kept": kept_stats,
        "delta_r_if_blocked": delta_r,
        "blocked_percent": pct(len(blocked), len(records)),
    }


def candidate_text(candidate: Dict[str, Any]) -> str:
    field = candidate["field"]
    value = candidate["value"]
    if candidate["kind"] == "CATEGORY_EQ":
        return f"{field} == {value} grubunu filtre adayı olarak blokla"
    if candidate["kind"] == "NUM_LT":
        return f"{field} < {value:g} ise filtre adayı olarak blokla"
    return f"{field} > {value:g} ise filtre adayı olarak blokla"


def candidate_key(candidate: Dict[str, Any]) -> str:
    return f"{candidate['kind']}|{candidate['field']}|{candidate['value']}"


def winner_loss_percent(full_records: Sequence[Dict[str, Any]], blocked_records: Sequence[Dict[str, Any]]) -> float:
    total_winners = sum(1 for r in full_records if outcome_class(r) == "WINNER")
    blocked_winners = sum(1 for r in blocked_records if outcome_class(r) == "WINNER")
    return float(pct(blocked_winners, total_winners) or 0.0)


def classify_candidate(
    all_records: Sequence[Dict[str, Any]],
    train: Sequence[Dict[str, Any]],
    holdout: Sequence[Dict[str, Any]],
    candidate: Dict[str, Any],
    train_eval: Dict[str, Any],
    holdout_eval: Dict[str, Any],
) -> Tuple[str, List[str]]:
    reasons: List[str] = []

    train_blocked, _ = apply_candidate(train, candidate)
    holdout_blocked, _ = apply_candidate(holdout, candidate)
    total_blocked, _ = apply_candidate(all_records, candidate)

    train_delta = safe_float(train_eval.get("delta_r_if_blocked"), None)
    holdout_delta = safe_float(holdout_eval.get("delta_r_if_blocked"), None)
    total_eval = evaluate_candidate_on(all_records, candidate)
    total_delta = safe_float(total_eval.get("delta_r_if_blocked"), None)

    total_summary = summarize_side(all_records)
    exact_cov = safe_float(total_summary.get("exact_r_coverage_percent"), 0.0) or 0.0
    winner_loss = winner_loss_percent(all_records, total_blocked)
    blocked_losers = sum(1 for r in total_blocked if outcome_class(r) == "LOSER")
    blocked_winners = sum(1 for r in total_blocked if outcome_class(r) == "WINNER")

    reasons.append(
        f"Toplamda {len(total_blocked)} işlemi engellerdi: {blocked_losers} SL, {blocked_winners} pozitif kapanış."
    )
    reasons.append(f"Kazanan kayıp oranı: %{winner_loss:.1f}.")
    if total_delta is not None:
        reasons.append(f"Geçmiş toplam R etkisi: {total_delta:+.3f}R.")
    if holdout_delta is not None:
        reasons.append(f"Son dönem holdout R etkisi: {holdout_delta:+.3f}R.")

    if not holdout:
        reasons.append("Zaman sıralı holdout örneği yetersiz.")
        return "YETERSIZ_VERI", reasons

    if (
        len(train_blocked) < RULES["candidate"]["min_blocked_train"]
        or train_eval["kept"]["records"] < RULES["candidate"]["min_kept_train"]
        or len(holdout_blocked) < RULES["candidate"]["min_blocked_holdout"]
        or holdout_eval["kept"]["records"] < RULES["candidate"]["min_kept_holdout"]
    ):
        reasons.append("Filtre etkisini güvenilir ölçmek için bloklanan/tutulan örnek sayısı yetersiz.")
        return "YETERSIZ_VERI", reasons

    # R hesabı yoksa yalnız kaybeden/kazanan oranıyla gölge adayı olabilir; canlı aday olamaz.
    if total_delta is None or train_delta is None or holdout_delta is None:
        if blocked_losers >= 4 and blocked_losers > blocked_winners * 2 and winner_loss <= 20.0:
            reasons.append("R kapsaması yetersiz; sadece sonuç dağılımı gölge testini destekliyor.")
            return "GOLGE_TEST", reasons
        return "REDDET", reasons

    # Eski veride iyi ama son dönemde kötü -> overfit uyarısı.
    if train_delta > 0 and holdout_delta <= 0:
        reasons.append("TRAIN'de iyi fakat son dönem HOLDOUT'ta doğrulanmadı; overfit riski.")
        return "REDDET_OVERFIT", reasons

    # İki dönemde de pozitif değilse reddet.
    if train_delta <= 0 or holdout_delta <= 0 or total_delta <= 0:
        reasons.append("Filtre etkisi hem eski hem yeni dönemde pozitif değil.")
        return "REDDET", reasons

    live = RULES["live_candidate"]
    if (
        len(all_records) >= live["min_total"]
        and len(holdout) >= live["min_holdout"]
        and exact_cov >= live["min_exact_r_coverage"] * 100.0
        and total_delta >= live["min_total_delta_r"]
        and holdout_delta >= live["min_holdout_delta_r"]
        and winner_loss <= live["max_winner_loss_percent"]
        and blocked_losers >= live["min_blocked_losers"]
    ):
        reasons.append("TRAIN ve HOLDOUT doğrulandı; sıkı kazanan-koruma eşiğini geçti.")
        return "CANLI_ADAY", reasons

    shadow = RULES["shadow"]
    if (
        len(all_records) >= shadow["min_total"]
        and exact_cov >= shadow["min_exact_r_coverage"] * 100.0
        and total_delta >= shadow["min_total_delta_r"]
        and holdout_delta >= shadow["min_holdout_delta_r"]
        and winner_loss <= shadow["max_winner_loss_percent"]
        and blocked_losers >= shadow["min_blocked_losers"]
    ):
        reasons.append("İki dönemde de olumlu; ancak doğrudan canlı için yeterince güçlü değil.")
        return "GOLGE_TEST", reasons

    # Exact R kapsaması düşük olsa bile güçlü loser seçiciliği varsa yalnız gölge.
    if blocked_losers >= 4 and blocked_losers > blocked_winners * 2 and winner_loss <= 20.0:
        reasons.append("Kaybeden seçiciliği umut verici; yalnız gölge test seviyesinde.")
        return "GOLGE_TEST", reasons

    return "IZLE", reasons


def make_numeric_candidates(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for feature in discover_numeric_features(records):
        values = [
            safe_float(get_path(record, feature), None)
            for record in records
        ]
        values = [v for v in values if v is not None]
        for threshold in candidate_thresholds(values):
            candidates.append({"kind": "NUM_LT", "field": feature, "value": threshold})
            candidates.append({"kind": "NUM_GT", "field": feature, "value": threshold})
    return candidates


def make_categorical_candidates(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for field in SAFE_CATEGORICAL_FIELDS:
        values = Counter(str(get_path(r, field) or "") for r in records)
        for value, count in values.items():
            if not value or count < 4:
                continue
            if count >= len(records) * 0.90:
                continue
            candidates.append({"kind": "CATEGORY_EQ", "field": field, "value": value})
    return candidates


def candidate_rank_key(item: Dict[str, Any]) -> Tuple:
    status_rank = {
        "CANLI_ADAY": 0,
        "GOLGE_TEST": 1,
        "IZLE": 2,
        "YETERSIZ_VERI": 3,
        "REDDET_OVERFIT": 4,
        "REDDET": 5,
    }
    total_delta = safe_float(item.get("total", {}).get("delta_r_if_blocked"), -999.0) or -999.0
    holdout_delta = safe_float(item.get("holdout", {}).get("delta_r_if_blocked"), -999.0) or -999.0
    blocked_losers = safe_int(item.get("total", {}).get("blocked", {}).get("losers"), 0)
    return (
        status_rank.get(item.get("status"), 9),
        -holdout_delta,
        -total_delta,
        -blocked_losers,
        item.get("candidate_key", ""),
    )


def analyze_filter_component(
    component: str,
    raw_records: Sequence[Dict[str, Any]],
    v1_decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    records = [r for r in raw_records if is_closed(r)]
    records = sorted(records, key=lambda r: (record_timestamp(r), str(r.get("id") or r.get("trade_id") or "")))

    # Premium için çok eski farklı sürümleri karıştırmamak adına en son 500 kapanışı kullan.
    if component == "PREMIUM" and len(records) > 500:
        records = records[-500:]
    if component in {"SCALP", "SWING"} and len(records) > 300:
        records = records[-300:]

    overall = summarize_side(records)
    train, holdout = chronological_split(records)

    result: Dict[str, Any] = {
        "component": component,
        "v1_decision_code": (v1_decision or {}).get("decision_code"),
        "records": len(records),
        "train_records": len(train),
        "holdout_records": len(holdout),
        "overall": overall,
        "auto_apply": False,
        "candidate_count_tested": 0,
        "prescriptions": [],
        "status": "NO_ROBUST_PRESCRIPTION",
    }

    if len(records) < RULES["split"]["min_total"] or not holdout:
        result["status"] = "YETERSIZ_VERI"
        result["reason"] = "Train/holdout reçete analizi için kapanmış işlem sayısı yetersiz."
        return result

    candidates = make_categorical_candidates(train) + make_numeric_candidates(train)
    seen = set()
    evaluated = []

    for candidate in candidates:
        key = candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)

        train_eval = evaluate_candidate_on(train, candidate)
        holdout_eval = evaluate_candidate_on(holdout, candidate)
        total_eval = evaluate_candidate_on(records, candidate)

        status, reasons = classify_candidate(
            records, train, holdout, candidate, train_eval, holdout_eval
        )

        evaluated.append({
            "candidate_key": key,
            "status": status,
            "prescription": candidate_text(candidate),
            "candidate": candidate,
            "reasons": reasons,
            "train": train_eval,
            "holdout": holdout_eval,
            "total": total_eval,
            "auto_apply": False,
        })

    result["candidate_count_tested"] = len(evaluated)

    actionable = [
        item for item in evaluated
        if item["status"] in {"CANLI_ADAY", "GOLGE_TEST"}
    ]
    actionable.sort(key=candidate_rank_key)

    # Birbirine çok benzeyen eşiklerden aynı feature için yalnız en iyi reçeteyi bırak.
    selected = []
    used_feature_direction = set()
    for item in actionable:
        cand = item["candidate"]
        dedup = (cand["field"], cand["kind"])
        if dedup in used_feature_direction:
            continue
        used_feature_direction.add(dedup)
        selected.append(item)
        if len(selected) >= RULES["candidate"]["max_candidates_per_component"]:
            break

    # V1 KORU diyorsa bu motor "CANLI_ADAY" bile bulsa doğrudan canlı önermez;
    # güvenli süreç gereği önce gölge seviyesine indirir.
    if (v1_decision or {}).get("decision_code") in {"KORU", "KORU_IZLE"}:
        for item in selected:
            if item["status"] == "CANLI_ADAY":
                item["status"] = "GOLGE_TEST"
                item["reasons"].append(
                    "V1 ana sistemi KORU dediği için kazanan profili bozmamak adına reçete önce gölgede denenmeli."
                )

    result["prescriptions"] = selected
    if selected:
        result["status"] = selected[0]["status"]
    else:
        # En iyi reddedilen iki adayı teşhis için göster.
        evaluated.sort(key=candidate_rank_key)
        result["rejected_examples"] = evaluated[:2]

    return result


def analyze_post_result(trade_ledger: Dict[str, Any]) -> Dict[str, Any]:
    records = iter_records(trade_ledger)
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for trade in records:
        follow = trade.get("post_result_shadow")
        if not isinstance(follow, dict):
            continue
        if str(follow.get("status") or "").upper() != "COMPLETED":
            continue
        final = normalize_outcome(follow.get("final_result") or trade.get("final_result"))
        groups[final].append(trade)

    prescriptions = []

    tp1 = groups.get("TP1_SONRASI_BE", [])
    if len(tp1) >= 10:
        tp2_reached = 0
        tp3_reached = 0
        mfe = []
        for trade in tp1:
            follow = trade.get("post_result_shadow") or {}
            reached = follow.get("reached_levels") if isinstance(follow.get("reached_levels"), dict) else {}
            tp2_reached += int("TP2" in reached)
            tp3_reached += int("TP3" in reached)
            mfe.append(follow.get("max_favorable_r"))

        tp2_rate = float(pct(tp2_reached, len(tp1)) or 0.0)
        tp3_rate = float(pct(tp3_reached, len(tp1)) or 0.0)
        avg_mfe = mean(mfe)

        if tp2_rate >= 35.0 or tp3_rate >= 20.0:
            prescriptions.append({
                "status": "GOLGE_TEST",
                "prescription": "TP1 sonrası BE ile kapanan kalan bölüm için küçük bir runner alternatifini SANAL olarak karşılaştır.",
                "evidence": {
                    "completed": len(tp1),
                    "later_tp2_rate_percent": tp2_rate,
                    "later_tp3_rate_percent": tp3_rate,
                    "avg_post_close_max_favorable_r": avg_mfe,
                },
                "implementation_note": (
                    "Mevcut BE kuralını değiştirme. Aynı kapanıştan sonra örneğin kalan pozisyonun "
                    "%10-%25 runner senaryolarını yalnız sanal PnL olarak hesapla."
                ),
                "auto_apply": False,
            })

    tp2 = groups.get("TP2_SONRASI_BE", [])
    if len(tp2) >= 10:
        tp3_reached = 0
        for trade in tp2:
            reached = (trade.get("post_result_shadow") or {}).get("reached_levels")
            reached = reached if isinstance(reached, dict) else {}
            tp3_reached += int("TP3" in reached)
        rate = float(pct(tp3_reached, len(tp2)) or 0.0)
        if rate >= 30.0:
            prescriptions.append({
                "status": "GOLGE_TEST",
                "prescription": "TP2 sonrası BE kapanan kalan bölüm için TP3 runner senaryosunu sanal test et.",
                "evidence": {
                    "completed": len(tp2),
                    "later_tp3_rate_percent": rate,
                },
                "auto_apply": False,
            })

    tp3 = groups.get("TP3", [])
    if len(tp3) >= 10:
        mfe = [safe_float((t.get("post_result_shadow") or {}).get("max_favorable_r"), None) for t in tp3]
        avg_mfe = mean(mfe)
        if avg_mfe is not None and avg_mfe >= 0.50:
            prescriptions.append({
                "status": "GOLGE_TEST",
                "prescription": "TP3'te tamamen kapanmak yerine küçük bir TP3-sonrası runner senaryosunu sanal test et.",
                "evidence": {
                    "completed": len(tp3),
                    "avg_post_tp3_max_favorable_r": avg_mfe,
                },
                "auto_apply": False,
            })

    return {
        "component": "POST_RESULT_MANAGEMENT",
        "completed_total": sum(len(v) for v in groups.values()),
        "group_counts": {k: len(v) for k, v in groups.items()},
        "status": prescriptions[0]["status"] if prescriptions else "IZLE",
        "prescriptions": prescriptions,
        "auto_apply": False,
    }


def analyze_portfolio(decision_report: Dict[str, Any]) -> Dict[str, Any]:
    component = (
        decision_report.get("components", {}).get("PORTFOLIO_RISK", {})
        if isinstance(decision_report.get("components"), dict)
        else {}
    )
    metrics = component.get("metrics") if isinstance(component.get("metrics"), dict) else {}
    by_code = metrics.get("by_block_code") if isinstance(metrics.get("by_block_code"), dict) else {}

    prescriptions = []
    for code, stats in by_code.items():
        if not isinstance(stats, dict):
            continue
        finding = str(stats.get("finding") or "")
        if finding == "GEREKSIZ_ENGEL_ADAYI" and safe_int(stats.get("records"), 0) >= 20:
            prescriptions.append({
                "status": "GOLGE_TEST",
                "prescription": f"{code} kuralı için mevcut limite göre DAHA GEVŞEK paralel gölge senaryo oluştur.",
                "evidence": {
                    "records": safe_int(stats.get("records"), 0),
                    "favorable_first_rate_percent": safe_float(stats.get("favorable_first_rate_percent"), None),
                    "adverse_first_rate_percent": safe_float(stats.get("adverse_first_rate_percent"), None),
                },
                "implementation_note": "Canlı portföy limiti değişmesin; yalnız karşı-olgusal ALLOW sonucu kaydedilsin.",
                "auto_apply": False,
            })
        elif finding == "ENGEL_FAYDALI_ADAYI" and safe_int(stats.get("records"), 0) >= 20:
            prescriptions.append({
                "status": "KORU",
                "prescription": f"{code} engelini koru.",
                "evidence": {
                    "records": safe_int(stats.get("records"), 0),
                    "favorable_first_rate_percent": safe_float(stats.get("favorable_first_rate_percent"), None),
                    "adverse_first_rate_percent": safe_float(stats.get("adverse_first_rate_percent"), None),
                },
                "auto_apply": False,
            })

    return {
        "component": "PORTFOLIO_RISK",
        "status": prescriptions[0]["status"] if prescriptions else "IZLE",
        "prescriptions": prescriptions,
        "auto_apply": False,
    }


def static_safety_prescriptions(decision_report: Dict[str, Any]) -> Dict[str, Any]:
    components = decision_report.get("components") if isinstance(decision_report.get("components"), dict) else {}
    output = {}

    range_decision = components.get("RANGE_SHADOW") if isinstance(components.get("RANGE_SHADOW"), dict) else {}
    if range_decision.get("decision_code") == "CANLIYA_ALMA_YENIDEN_TASARLA":
        output["RANGE_SHADOW"] = {
            "component": "RANGE_SHADOW",
            "status": "REDDET_MEVCUT_SURUM",
            "prescriptions": [{
                "status": "REDDET_MEVCUT_SURUM",
                "prescription": "Range V3'ün mevcut giriş/stop mimarisini canlıya taşımayı bırak; yeni sürümü ayrı gölge motor olarak tasarla.",
                "evidence": range_decision.get("metrics", {}),
                "auto_apply": False,
            }],
            "auto_apply": False,
        }

    momentum = components.get("MOMENTUM_SHADOW") if isinstance(components.get("MOMENTUM_SHADOW"), dict) else {}
    if momentum.get("decision_code") == "GOLGEDE_TUT_CANLIYA_ALMA":
        output["MOMENTUM_SHADOW"] = {
            "component": "MOMENTUM_SHADOW",
            "status": "CANLIYA_ALMA",
            "prescriptions": [{
                "status": "CANLIYA_ALMA",
                "prescription": "Mevcut Momentum would_block kararlarını canlı engel yapma; kazanan kesme oranı düşene kadar gölgede tut.",
                "evidence": momentum.get("metrics", {}),
                "auto_apply": False,
            }],
            "auto_apply": False,
        }

    return output


def top_prescriptions(components: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rank = {
        "CANLI_ADAY": 1,
        "GOLGE_TEST": 2,
        "KORU": 3,
        "IZLE": 4,
        "CANLIYA_ALMA": 5,
        "REDDET_MEVCUT_SURUM": 5,
        "NO_ROBUST_PRESCRIPTION": 6,
        "YETERSIZ_VERI": 7,
    }
    rows = []
    for component, result in components.items():
        prescriptions = result.get("prescriptions") if isinstance(result.get("prescriptions"), list) else []
        for item in prescriptions:
            if not isinstance(item, dict):
                continue
            rows.append({
                "component": component,
                "status": item.get("status"),
                "prescription": item.get("prescription"),
                "evidence": item.get("evidence") or {
                    "total": item.get("total"),
                    "holdout": item.get("holdout"),
                },
                "auto_apply": False,
            })
    rows.sort(key=lambda item: (rank.get(str(item.get("status")), 9), item["component"], str(item.get("prescription"))))
    return rows[:15]


def build_report(base_dir: str = ".", current_ts: Optional[int] = None) -> Dict[str, Any]:
    current_ts = int(current_ts or now_ts())
    root = Path(base_dir)

    loaded: Dict[str, Dict[str, Any]] = {}
    errors = {}
    for name, filename in FILES.items():
        data, error = load_json(root / filename)
        loaded[name] = data
        if error:
            errors[name] = error

    decision_report = loaded["decision_report"]
    v1_components = (
        decision_report.get("components")
        if isinstance(decision_report.get("components"), dict)
        else {}
    )

    components: Dict[str, Dict[str, Any]] = {}
    for name, source_name in (
        ("PREMIUM", "premium"),
        ("SCALP", "scalp"),
        ("SWING", "swing"),
        ("PUMP_DUMP", "pump"),
    ):
        components[name] = analyze_filter_component(
            name,
            iter_records(loaded[source_name]),
            v1_components.get(name) if isinstance(v1_components.get(name), dict) else {},
        )

    components["POST_RESULT_MANAGEMENT"] = analyze_post_result(loaded["premium"])
    components["PORTFOLIO_RISK"] = analyze_portfolio(decision_report)
    components.update(static_safety_prescriptions(decision_report))

    report = {
        "version": VERSION,
        "mode": MODE,
        "generated_at": current_ts,
        "generated_at_utc": datetime.fromtimestamp(current_ts, tz=timezone.utc).isoformat(),
        "auto_apply": False,
        "source_decision_version": decision_report.get("version"),
        "data_errors": errors,
        "rules": RULES,
        "methodology": {
            "split": "chronological 70% TRAIN / 30% HOLDOUT",
            "candidate_generation": "giriş anında bilinen güvenli sayısal/categorical alanlar",
            "leakage_protection": "sonuç sonrası MFE/MAE, diagnosis, outcome, snapshot vb. feature olarak kullanılmaz",
            "winner_protection": "kazanan kayıp oranı yüksek filtre CANLI_ADAY olamaz",
            "overfit_protection": "TRAIN pozitif fakat HOLDOUT negatif/0 ise REDDET_OVERFIT",
            "live_rule": "CANLI_ADAY bile otomatik uygulanmaz; kullanıcı onayı + tercihen gölge doğrulama gerekir",
        },
        "components": components,
        "top_prescriptions": top_prescriptions(components),
        "notes": [
            "Reçete Motoru kaynak kodu değiştirmez.",
            "CANLI_ADAY bir otomatik uygulama emri değildir.",
            "R kapsaması düşük botlarda sonuç proxy-R yalnız aday sıralamak için kullanılır ve canlıya alma eşiği daha sıkıdır.",
            "V1 KORU dediği bir botta bulunan aday eşik, kazanan profili korumak için önce GOLGE_TEST seviyesine düşürülür.",
        ],
    }
    return report


def print_report(report: Dict[str, Any]) -> None:
    print("=" * 88)
    print("REÇETE MOTORU V2")
    print(report.get("version"))
    print("=" * 88)
    top = report.get("top_prescriptions", [])
    if not top:
        print("Robust reçete adayı yok.")
    for item in top:
        print(f"{item.get('component', ''):24} {item.get('status', ''):18} {item.get('prescription', '')}")
    if report.get("data_errors"):
        print("-" * 88)
        print("VERİ UYARILARI:", report["data_errors"])
    print("=" * 88)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto bot Reçete Motoru V2")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args()

    report = build_report(args.base_dir)
    print_report(report)

    if not args.print_only:
        output = Path(args.output)
        if not output.is_absolute():
            output = Path(args.base_dir) / output
        save_json_atomically(output, report)
        print("Reçete raporu kaydedildi:", output)


if __name__ == "__main__":
    main()
