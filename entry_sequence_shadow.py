"""Entry Sequence Shadow V1.

Analysis-only sidecar. It does not send Telegram messages, open orders, or
change live signal rules. It freezes the adverse/favorable path BEFORE TP1 so
we can distinguish:
- direction/setup failure before TP1
- correct direction with clean entry
- correct direction but early/pressured entry
- post-TP bounce (which must not be confused with early entry)
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

VERSION = "ENTRY_SEQUENCE_SHADOW_V1_2026_08_18"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_RULE_CHANGE"
DEFAULT_OUTPUT = "entry_sequence_shadow.json"
MIN_DECISION_SAMPLE = 30

POSITIVE_RESULTS = {"TP1_SONRASI_BE", "TP2_SONRASI_BE", "TP3"}
CLOSED_RESULTS = POSITIVE_RESULTS | {"SL", "EXPIRED"}


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def atomic_save(path: str, data: dict[str, Any]) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        json.loads(tmp_path.read_text(encoding="utf-8"))
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def empty_state() -> dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "auto_apply": False,
        "minimum_decision_sample": MIN_DECISION_SAMPLE,
        "records": {},
        "summary": {},
    }


def normalize_state(raw: Any) -> dict[str, Any]:
    state = raw if isinstance(raw, dict) else empty_state()
    state["version"] = VERSION
    state["mode"] = MODE
    state["auto_apply"] = False
    state["minimum_decision_sample"] = MIN_DECISION_SAMPLE
    if not isinstance(state.get("records"), dict):
        state["records"] = {}
    if not isinstance(state.get("summary"), dict):
        state["summary"] = {}
    return state


def ledger_index(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    trades = ledger.get("trades") or {}
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(trades, dict):
        return result
    for key, value in trades.items():
        if not isinstance(value, dict):
            continue
        result[str(key)] = value
        tid = str(value.get("trade_id") or "")
        if tid:
            result[tid] = value
    return result


def classify_winner_pre_tp1_mae(mae_r: float | None) -> str:
    if mae_r is None:
        return "UNKNOWN"
    if mae_r <= 0.25:
        return "CLEAN_ENTRY"
    if mae_r <= 0.50:
        return "NORMAL_PULLBACK"
    if mae_r <= 0.75:
        return "EARLY_ENTRY_PRESSURE"
    return "HEAVY_EARLY_ENTRY_PRESSURE"


def _signal_record(trade_id: str, signal: dict[str, Any], now_ts: int) -> dict[str, Any]:
    tp1_already_hit = bool(signal.get("tp1_hit"))
    opened_at = safe_int(signal.get("opened_at"), 0)
    return {
        "trade_id": trade_id,
        "system": "PREMIUM_MTF",
        "symbol": signal.get("symbol"),
        "direction": signal.get("direction"),
        "source": signal.get("source"),
        "quality": signal.get("quality"),
        "score": safe_float(signal.get("score")),
        "entry": safe_float(signal.get("entry")),
        "sl": safe_float(signal.get("sl")),
        "tp1": safe_float(signal.get("tp1")),
        "opened_at": opened_at,
        "first_seen_at": now_ts,
        "sequence_quality": (
            "BACKFILL_UNRELIABLE" if tp1_already_hit else "LIVE_OBSERVED"
        ),
        "pre_tp1_frozen": tp1_already_hit,
        "pre_tp1_max_adverse_r": None,
        "pre_tp1_best_favorable_r": None,
        "tp1_hit_at": safe_int(signal.get("tp1_hit_at"), 0) or None,
        "time_to_tp1_minutes": None,
        "first_favorable_0_25r_at": None,
        "first_favorable_0_50r_at": None,
        "first_adverse_0_25r_at": None,
        "first_adverse_0_50r_at": None,
        "entry_distance_at_send_percent": safe_float(
            signal.get("entry_distance_at_send_percent")
        ),
        "zone_distance_percent": safe_float(signal.get("zone_distance_percent")),
        "rsi_15m": safe_float(signal.get("rsi_15m")),
        "adx_15m": safe_float(signal.get("adx_15m")),
        "adx_1h": safe_float(signal.get("adx_1h")),
        "adx_4h": safe_float(signal.get("adx_4h")),
        "volume_ratio": safe_float(signal.get("volume_ratio")),
        "resolved": False,
        "final_result": None,
        "closed_at": None,
        "r_result": None,
        "classification": (
            "UNRESOLVED_SEQUENCE" if tp1_already_hit else "TRACKING_PRE_TP1"
        ),
        "last_seen_at": now_ts,
    }


def _update_open_record(
    record: dict[str, Any], signal: dict[str, Any], now_ts: int
) -> None:
    record["last_seen_at"] = now_ts
    if record.get("sequence_quality") != "LIVE_OBSERVED":
        return

    if bool(record.get("pre_tp1_frozen")):
        return

    favorable = max(0.0, safe_float(signal.get("best_favorable_r"), 0.0) or 0.0)
    adverse = max(0.0, safe_float(signal.get("worst_adverse_r"), 0.0) or 0.0)

    old_fav = safe_float(record.get("pre_tp1_best_favorable_r"), 0.0) or 0.0
    old_adv = safe_float(record.get("pre_tp1_max_adverse_r"), 0.0) or 0.0
    record["pre_tp1_best_favorable_r"] = round(max(old_fav, favorable), 6)
    record["pre_tp1_max_adverse_r"] = round(max(old_adv, adverse), 6)

    stamp = safe_int(signal.get("last_checked_at"), 0) or now_ts
    if favorable >= 0.25 and not record.get("first_favorable_0_25r_at"):
        record["first_favorable_0_25r_at"] = stamp
    if favorable >= 0.50 and not record.get("first_favorable_0_50r_at"):
        record["first_favorable_0_50r_at"] = stamp
    if adverse >= 0.25 and not record.get("first_adverse_0_25r_at"):
        record["first_adverse_0_25r_at"] = stamp
    if adverse >= 0.50 and not record.get("first_adverse_0_50r_at"):
        record["first_adverse_0_50r_at"] = stamp

    if bool(signal.get("tp1_hit")):
        hit_at = safe_int(signal.get("tp1_hit_at"), 0) or stamp
        record["tp1_hit_at"] = hit_at
        opened_at = safe_int(record.get("opened_at"), 0)
        if opened_at and hit_at >= opened_at:
            record["time_to_tp1_minutes"] = round((hit_at - opened_at) / 60.0, 2)
        record["pre_tp1_frozen"] = True
        record["classification"] = classify_winner_pre_tp1_mae(
            safe_float(record.get("pre_tp1_max_adverse_r"))
        )


def _resolve_from_ledger(
    record: dict[str, Any], trade: dict[str, Any], now_ts: int
) -> None:
    if str(trade.get("status") or "").upper() != "CLOSED":
        return
    result = str(trade.get("final_result") or "").upper()
    if result not in CLOSED_RESULTS:
        return

    record["resolved"] = True
    record["final_result"] = result
    record["closed_at"] = safe_int(trade.get("closed_at"), now_ts)
    record["r_result"] = safe_float(trade.get("r_result"))

    if record.get("sequence_quality") != "LIVE_OBSERVED":
        record["classification"] = "UNRESOLVED_SEQUENCE"
        return

    if result in POSITIVE_RESULTS:
        record["classification"] = classify_winner_pre_tp1_mae(
            safe_float(record.get("pre_tp1_max_adverse_r"))
        )
    elif result == "SL":
        if not record.get("tp1_hit_at"):
            record["classification"] = "FAILED_BEFORE_TP1"
        else:
            record["classification"] = "POST_TP1_PROTECTION_FAILURE"
    else:
        record["classification"] = "EXPIRED"


def update_state(
    state: dict[str, Any],
    open_signals: dict[str, Any],
    ledger: dict[str, Any],
    now_ts: int | None = None,
) -> dict[str, Any]:
    now_ts = int(now_ts or time.time())
    state = normalize_state(state)
    records = state["records"]
    lindex = ledger_index(ledger)

    if not isinstance(open_signals, dict):
        open_signals = {}

    for key, signal in open_signals.items():
        if not isinstance(signal, dict):
            continue
        trade_id = str(signal.get("trade_id") or key)
        record = records.get(trade_id)
        if not isinstance(record, dict):
            record = _signal_record(trade_id, signal, now_ts)
            records[trade_id] = record
        _update_open_record(record, signal, now_ts)

    for trade_id, record in list(records.items()):
        if not isinstance(record, dict) or record.get("resolved"):
            continue
        trade = lindex.get(trade_id)
        if trade:
            _resolve_from_ledger(record, trade, now_ts)

    if len(records) > 500:
        ordered = sorted(
            records.items(),
            key=lambda kv: (
                0 if kv[1].get("resolved") else 1,
                safe_int(kv[1].get("closed_at") or kv[1].get("opened_at"), 0),
            ),
        )
        for trade_id, _ in ordered[: len(records) - 500]:
            records.pop(trade_id, None)

    state["summary"] = build_summary(records)
    state["updated_at"] = now_ts
    return state


def build_summary(records: dict[str, Any]) -> dict[str, Any]:
    reliable = [
        r
        for r in records.values()
        if isinstance(r, dict)
        and r.get("resolved")
        and r.get("sequence_quality") == "LIVE_OBSERVED"
    ]
    counts = Counter(str(r.get("classification") or "UNKNOWN") for r in reliable)
    winners = [r for r in reliable if str(r.get("final_result")) in POSITIVE_RESULTS]
    maes = [
        safe_float(r.get("pre_tp1_max_adverse_r"))
        for r in winners
        if safe_float(r.get("pre_tp1_max_adverse_r")) is not None
    ]
    high_pressure = sum(1 for x in maes if x is not None and x > 0.50)
    failed_before_tp1 = counts.get("FAILED_BEFORE_TP1", 0)
    sample = len(reliable)

    by_source = defaultdict(Counter)
    by_direction = defaultdict(Counter)
    for r in reliable:
        cls = str(r.get("classification") or "UNKNOWN")
        by_source[str(r.get("source") or "UNKNOWN")][cls] += 1
        by_direction[str(r.get("direction") or "UNKNOWN")][cls] += 1

    if sample < MIN_DECISION_SAMPLE:
        decision = "VERI_TOPLA"
        next_action = (
            f"En az {MIN_DECISION_SAMPLE} güvenilir kapanış biriktir; canlı giriş/yön "
            "kuralını otomatik değiştirme."
        )
    else:
        high_share = high_pressure / len(maes) * 100.0 if maes else 0.0
        fail_share = failed_before_tp1 / sample * 100.0 if sample else 0.0
        if high_share >= 25.0:
            decision = "ENTRY_CONFIRM_SHADOW_REVIEW"
            next_action = (
                "Kazananlarda TP1 öncesi >0.50R ters baskı yüksek; giriş teyidi "
                "adaylarını gölgede test et, yön filtresini genelleme."
            )
        elif fail_share >= 35.0:
            decision = "DIRECTION_SETUP_SHADOW_REVIEW"
            next_action = (
                "TP1 öncesi başarısızlık yüksek; 4H/1H yön + hacim/momentum "
                "özelliklerini kazanan-kaybeden ayrımında karşılaştır."
            )
        else:
            decision = "KORU_IZLE"
            next_action = "Mevcut canlı kuralları koru; giriş yolunu izlemeye devam et."

    return {
        "reliable_resolved_sample": sample,
        "backfill_unreliable_records": sum(
            1
            for r in records.values()
            if isinstance(r, dict) and r.get("sequence_quality") == "BACKFILL_UNRELIABLE"
        ),
        "classification_counts": dict(sorted(counts.items())),
        "winner_sample": len(winners),
        "winner_pre_tp1_mae_sample": len(maes),
        "avg_winner_pre_tp1_mae_r": round(sum(maes) / len(maes), 4) if maes else None,
        "winner_pre_tp1_mae_over_0_50r": high_pressure,
        "winner_pre_tp1_mae_over_0_50r_rate_percent": (
            round(high_pressure / len(maes) * 100.0, 2) if maes else 0.0
        ),
        "failed_before_tp1": failed_before_tp1,
        "failed_before_tp1_rate_percent": (
            round(failed_before_tp1 / sample * 100.0, 2) if sample else 0.0
        ),
        "by_source": {k: dict(sorted(v.items())) for k, v in sorted(by_source.items())},
        "by_direction": {
            k: dict(sorted(v.items())) for k, v in sorted(by_direction.items())
        },
        "decision": decision,
        "next_action": next_action,
        "minimum_decision_sample": MIN_DECISION_SAMPLE,
        "auto_apply": False,
        "note": (
            "Sadece TP1 öncesi gözlenen yol kullanılır. TP1 sonrası ters hareket "
            "erken giriş olarak sayılmaz."
        ),
    }


def main() -> None:
    state = normalize_state(load_json(DEFAULT_OUTPUT, empty_state()))
    open_signals = load_json("open_signals.json", {})
    ledger = load_json("trade_ledger.json", {})
    state = update_state(state, open_signals, ledger)
    atomic_save(DEFAULT_OUTPUT, state)
    summary = state.get("summary") or {}
    print(
        "ENTRY_SEQUENCE_SHADOW",
        "sample=", summary.get("reliable_resolved_sample", 0),
        "decision=", summary.get("decision", "VERI_TOPLA"),
        "auto_apply=false",
    )


if __name__ == "__main__":
    main()
