"""Truthful final-admission diagnostics for Market First.

Internal ENTRY promotion is not the same thing as a Telegram delivery attempt.
The legacy observability report intentionally tracked the broad preparation
funnel, but its generic ``PROMOTED_BUT_FINAL_SEND_NOT_CONFIRMED`` bucket can be
misread as a Telegram failure even when later Profit Quality, Final Execution or
Capital Survival gates correctly rejected the candidate.

This module keeps those concepts separate. It is observational only: it never
changes a strategy threshold, promotes a candidate, sends Telegram, places an
exchange order or widens a stop.
"""
from __future__ import annotations

from collections import Counter
import json
import os
import tempfile
import time
from typing import Any, Dict, Mapping, Optional

VERSION = "MARKET_FIRST_DELIVERY_TRUTH_V1_2026_09_16"
OUTPUT_FILE = "market_first_delivery_truth.json"

CONVERSION_FILE = "market_first_entry_conversion_report.json"
OPPORTUNITY_FILE = "market_first_opportunity_summary.json"
PROFIT_QUALITY_FILE = "market_first_profit_quality.json"
FINAL_EXECUTION_FILE = "market_first_final_execution_gate.json"
SURVIVAL_FILE = "market_first_profit_survival_gate.json"
SURVIVAL_V2_FILE = "market_first_profit_survival_v2.json"


def _si(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _atomic_save(path: str, payload: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".delivery_truth.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _run_counts(payload: Mapping[str, Any] | None) -> Dict[str, int]:
    raw = payload.get("run_counts") if isinstance(payload, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, int] = {}
    for key, value in raw.items():
        count = _si(value)
        if count:
            out[str(key)] = count
    return out


def _accepted_count(payload: Mapping[str, Any] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    accepted = payload.get("accepted")
    if isinstance(accepted, list):
        return len(accepted)
    return _si(_run_counts(payload).get("ACCEPTED"))


def _meaningful_blocks(counts: Mapping[str, int]) -> Dict[str, int]:
    ignored = {
        "ACCEPTED",
        "OK",
        "OK_SHADOW_EDGE",
        "NORMAL_PASS",
        # This means a previous layer rejected the candidate. Counting it again
        # as a new blocker would double-count the same funnel loss.
        "UPSTREAM_REJECTED",
    }
    return {
        str(key): _si(value)
        for key, value in counts.items()
        if str(key) not in ignored and _si(value) > 0
    }


def _top_blocker(layers: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any] | None:
    rows = []
    for layer_name, layer in layers.items():
        blocks = layer.get("blocks") if isinstance(layer, Mapping) else None
        if not isinstance(blocks, Mapping):
            continue
        for reason, count in blocks.items():
            rows.append((int(count), str(layer_name), str(reason)))
    if not rows:
        return None
    count, layer_name, reason = max(rows)
    return {"layer": layer_name, "reason": reason, "count": count}


def build_report(
    conversion: Mapping[str, Any] | None,
    opportunity: Mapping[str, Any] | None,
    profit_quality: Mapping[str, Any] | None,
    final_execution: Mapping[str, Any] | None,
    survival: Mapping[str, Any] | None,
    survival_v2: Mapping[str, Any] | None,
    *,
    generated_at: Optional[int] = None,
) -> Dict[str, Any]:
    conversion = conversion if isinstance(conversion, Mapping) else {}
    opportunity = opportunity if isinstance(opportunity, Mapping) else {}
    profit_quality = profit_quality if isinstance(profit_quality, Mapping) else {}
    final_execution = final_execution if isinstance(final_execution, Mapping) else {}
    survival = survival if isinstance(survival, Mapping) else {}
    survival_v2 = survival_v2 if isinstance(survival_v2, Mapping) else {}

    funnel = conversion.get("funnel") if isinstance(conversion.get("funnel"), Mapping) else {}
    clean = opportunity.get("entry_plan_clean") if isinstance(opportunity.get("entry_plan_clean"), Mapping) else {}
    broad = opportunity.get("entry_plan") if isinstance(opportunity.get("entry_plan"), Mapping) else {}

    promoted = _si(funnel.get("entry_promoted"), _si(clean.get("entry_promoted")))
    sent = _si(funnel.get("entry_signal_sent"), _si(clean.get("entry_signal_sent")))
    promoted_unsent = max(0, promoted - sent)

    # Clean summary may not expose failures on old snapshots, so fall back to
    # the broad ledger. A failed send is a real delivery attempt; a promoted
    # candidate with no send result is merely an internal admission gap.
    send_failed = _si(clean.get("entry_send_failed"), _si(broad.get("entry_send_failed")))
    send_failed = min(send_failed, promoted_unsent)
    internal_final_admission_gap = max(0, promoted_unsent - send_failed)

    pq_counts = _run_counts(profit_quality)
    final_counts = _run_counts(final_execution)
    survival_counts = _run_counts(survival)

    layers: Dict[str, Dict[str, Any]] = {
        "profit_quality": {
            "accepted": _accepted_count(profit_quality),
            "run_counts": pq_counts,
            "blocks": _meaningful_blocks(pq_counts),
        },
        "final_execution": {
            "accepted": _accepted_count(final_execution),
            "run_counts": final_counts,
            "blocks": _meaningful_blocks(final_counts),
        },
        "capital_survival": {
            "accepted": _accepted_count(survival),
            "run_counts": survival_counts,
            "blocks": _meaningful_blocks(survival_counts),
        },
    }

    health = survival_v2.get("health") if isinstance(survival_v2.get("health"), Mapping) else {}
    if not health:
        health = survival.get("health") if isinstance(survival.get("health"), Mapping) else {}
    survival_mode = str(health.get("mode") or "UNKNOWN").upper()
    survival_reason = str(health.get("reason") or "")

    if survival_mode == "HALT":
        status = "CAPITAL_HALT"
    elif survival_mode == "RECOVERY_STRICT":
        status = "CAPITAL_RECOVERY_STRICT"
    elif send_failed > 0:
        status = "TELEGRAM_DELIVERY_FAILURE_PRESENT"
    elif internal_final_admission_gap > 0:
        status = "FINAL_ADMISSION_GAP"
    else:
        status = "OK"

    return {
        "version": VERSION,
        "generated_at": int(generated_at if generated_at is not None else time.time()),
        "status": status,
        "survival": {
            "mode": survival_mode,
            "reason": survival_reason,
        },
        "funnel_truth": {
            "internal_promoted": promoted,
            "real_signal_sent": sent,
            "promoted_unsent": promoted_unsent,
            "real_telegram_send_failures": send_failed,
            "internal_final_admission_gap": internal_final_admission_gap,
            "promotion_is_send_attempt": False,
        },
        "latest_gate_layers": layers,
        "top_latest_blocker": _top_blocker(layers),
        "interpretation": {
            "promoted_unsent": (
                "Internal ENTRY promotion that did not become a confirmed real Telegram signal. "
                "It must not be interpreted as a Telegram transport failure."
            ),
            "real_telegram_send_failures": (
                "Ledger rows where the system actually attempted the final send path and recorded failure."
            ),
            "capital_rule": (
                "RECOVERY_STRICT/HALT remains authoritative; this diagnostic never relaxes it."
            ),
        },
        "strategy_mutation": False,
        "exchange_orders": False,
        "stop_widening": False,
    }


def run() -> Dict[str, Any]:
    report = build_report(
        _load(CONVERSION_FILE, {}),
        _load(OPPORTUNITY_FILE, {}),
        _load(PROFIT_QUALITY_FILE, {}),
        _load(FINAL_EXECUTION_FILE, {}),
        _load(SURVIVAL_FILE, {}),
        _load(SURVIVAL_V2_FILE, {}),
    )
    _atomic_save(OUTPUT_FILE, report)
    print("MARKET FIRST DELIVERY TRUTH:", report)
    return report


if __name__ == "__main__":
    run()
