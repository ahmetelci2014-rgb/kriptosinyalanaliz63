"""Direction-aware diagnostics for Premium Early Breakout outcomes.

Shadow/research only. This module reads the existing result diagnostics already
stored in trade_ledger.json and summarizes them separately for LONG and SHORT
Early Breakout trades. It never sends Telegram messages, never places orders,
and never changes live Entry/TP/SL/BE eligibility.

The goal is to distinguish three problems before changing the live strategy:
- direction/setup invalidation after SL,
- correct direction but entry/stop timing too early/tight,
- break-even protection that may have cut a continuing move too early.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, Iterable, Optional

VERSION = "EARLY_BREAKOUT_DIRECTION_DIAGNOSTICS_V1_2026_08_24"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_RULE_MUTATION"
LEDGER_FILE = "trade_ledger.json"
STATE_FILE = "early_breakout_direction_diagnostics.json"
SOURCE = "EARLY_BREAKOUT_ENTRY"
WINDOW_HOURS = 24

SL_RECOVERY_CODES = {
    "SL_SONRASI_GUCLU_TOPARLANMA",
    "SL_SONRASI_TOPARLANMA",
}
SL_DIRECTION_CODES = {
    "SL_SONRASI_TERS_YON_DEVAMI",
}
BE_EARLY_CODES = {
    "BE_SONRASI_TP3",
    "BE_SONRASI_YENIDEN_HEDEF",
}
BE_PROTECT_CODES = {
    "BE_KORUMASI_DOGRU",
}


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".early_direction_diag.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
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


def _canon(value: Any) -> str:
    raw = str(value or "").upper().strip()
    if raw == "STOP":
        return "SL"
    if raw == "BREAK_EVEN":
        return "BE"
    return raw


def _empty_direction() -> Dict[str, Any]:
    return {
        "tracked_diagnostics": 0,
        "completed_diagnostics": 0,
        "sl_diagnostics": 0,
        "be_diagnostics": 0,
        "sl_recovery_timing": 0,
        "sl_direction_or_setup": 0,
        "be_maybe_early": 0,
        "be_protection_correct": 0,
        "provisional_or_mixed": 0,
        "diagnosis_codes": {},
        "likely_causes": {},
    }


def _summarize(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    out = {"LONG": _empty_direction(), "SHORT": _empty_direction()}
    code_counts = {"LONG": Counter(), "SHORT": Counter()}
    cause_counts = {"LONG": Counter(), "SHORT": Counter()}

    for trade in trades:
        if not isinstance(trade, dict):
            continue
        if str(trade.get("source") or "").upper() != SOURCE:
            continue
        direction = str(trade.get("direction") or "").upper()
        if direction not in out:
            continue
        diag = trade.get("result_diagnostics")
        if not isinstance(diag, dict):
            continue
        diagnosis = diag.get("diagnosis") if isinstance(diag.get("diagnosis"), dict) else {}
        code = str(diagnosis.get("code") or "UNKNOWN").upper()
        cause = str(diagnosis.get("likely_cause") or "UNKNOWN").upper()
        final_result = _canon(diag.get("final_result") or trade.get("final_result"))
        status = str(diag.get("status") or "").upper()

        row = out[direction]
        row["tracked_diagnostics"] += 1
        if status == "COMPLETED":
            row["completed_diagnostics"] += 1
        if final_result == "SL":
            row["sl_diagnostics"] += 1
        elif final_result in {"BE", "TP1_SONRASI_BE", "TP2_SONRASI_BE"}:
            row["be_diagnostics"] += 1

        code_counts[direction][code] += 1
        cause_counts[direction][cause] += 1

        if code in SL_RECOVERY_CODES:
            row["sl_recovery_timing"] += 1
        elif code in SL_DIRECTION_CODES:
            row["sl_direction_or_setup"] += 1
        elif code in BE_EARLY_CODES:
            row["be_maybe_early"] += 1
        elif code in BE_PROTECT_CODES:
            row["be_protection_correct"] += 1
        else:
            row["provisional_or_mixed"] += 1

    for direction in out:
        out[direction]["diagnosis_codes"] = dict(code_counts[direction])
        out[direction]["likely_causes"] = dict(cause_counts[direction])
        completed = int(out[direction]["completed_diagnostics"] or 0)
        if completed:
            out[direction]["direction_setup_share_of_completed_percent"] = round(
                out[direction]["sl_direction_or_setup"] / completed * 100.0, 2
            )
            out[direction]["timing_recovery_share_of_completed_percent"] = round(
                out[direction]["sl_recovery_timing"] / completed * 100.0, 2
            )
        else:
            out[direction]["direction_setup_share_of_completed_percent"] = 0.0
            out[direction]["timing_recovery_share_of_completed_percent"] = 0.0
    return out


def _watch_flags(summary: Dict[str, Any]) -> list[str]:
    flags: list[str] = []
    for direction in ("LONG", "SHORT"):
        row = summary.get(direction) if isinstance(summary.get(direction), dict) else {}
        completed = int(row.get("completed_diagnostics") or 0)
        wrong = int(row.get("sl_direction_or_setup") or 0)
        timing = int(row.get("sl_recovery_timing") or 0)
        be_early = int(row.get("be_maybe_early") or 0)
        # These are research alerts only. They never block or promote a trade.
        if completed >= 4 and wrong >= 3 and wrong / completed >= 0.50:
            flags.append(f"{direction}_DIRECTION_SETUP_WATCH")
        if completed >= 4 and timing >= 3 and timing / completed >= 0.50:
            flags.append(f"{direction}_ENTRY_STOP_TIMING_WATCH")
        if completed >= 4 and be_early >= 3 and be_early / completed >= 0.50:
            flags.append(f"{direction}_BE_EARLY_WATCH")
    return flags


def build_state(ledger: Dict[str, Any], *, now_ts: Optional[int] = None) -> Dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    trades_map = ledger.get("trades") if isinstance(ledger.get("trades"), dict) else {}
    early_trades = [
        trade
        for trade in trades_map.values()
        if isinstance(trade, dict) and str(trade.get("source") or "").upper() == SOURCE
    ]
    recent_cutoff = now - WINDOW_HOURS * 3600
    recent = [
        trade
        for trade in early_trades
        if int(trade.get("closed_at") or 0) >= recent_cutoff
    ]

    lifetime = _summarize(early_trades)
    last_window = _summarize(recent)
    flags = _watch_flags(last_window)
    return {
        "version": VERSION,
        "mode": MODE,
        "updated_at": now,
        "window_hours": WINDOW_HOURS,
        "scope_note": "Only Early Breakout SL/BE trades that already have result_diagnostics are classified here; TP3 winners are not part of this diagnostic denominator.",
        "lifetime": lifetime,
        "last_24h": last_window,
        "watch_flags": flags,
        "live_rule_action": "NONE_SHADOW_ONLY",
    }


def run(
    ledger_file: str = LEDGER_FILE,
    state_file: str = STATE_FILE,
    *,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    state = build_state(_load(ledger_file), now_ts=now_ts)
    _atomic_save(state_file, state)
    return state


def main() -> None:
    state = run()
    print(
        "EARLY BREAKOUT DIRECTION DIAGNOSTICS:",
        {
            "last_24h": state.get("last_24h"),
            "watch_flags": state.get("watch_flags"),
            "live_rule_action": state.get("live_rule_action"),
        },
    )


if __name__ == "__main__":
    main()
