"""Observation-only 4H alignment study for Day Trading candidates.

2H remains the live direction engine. This file only annotates candidate rows
with the completed-4H direction so later results can compare 2H-only setups
against 2H+4H aligned setups without changing live admission.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

import day_trading_core as core
import day_trading_candidate_tracker as tracker

SUMMARY_FILE = "day_trading_4h_shadow_summary.json"
VERSION = "DAY_TRADING_4H_SHADOW_V1_2026_09_27"


def _summary(ledger: Mapping[str, Any]) -> dict[str, Any]:
    rows_map = ledger.get("candidates") if isinstance(ledger.get("candidates"), Mapping) else {}
    rows = [row for row in rows_map.values() if isinstance(row, Mapping) and row.get("direction_4h") is not None]
    aligned = [row for row in rows if row.get("aligned_4h") is True]
    not_aligned = [row for row in rows if row.get("aligned_4h") is False]

    def bucket(items: list[Mapping[str, Any]]) -> dict[str, Any]:
        closed = [row for row in items if row.get("status") == "CLOSED"]
        net = [core._sf(row.get("net_r")) for row in closed]
        results = Counter(str(row.get("final_result") or "UNKNOWN") for row in closed)
        return {
            "sample": len(items),
            "closed": len(closed),
            "total_net_r": round(sum(net), 4),
            "expectancy_net_r": round(sum(net) / len(net), 4) if net else 0.0,
            "results": dict(results),
        }

    return {
        "version": VERSION,
        "generated_at": core._now(),
        "annotated_candidates": len(rows),
        "aligned_4h": bucket(aligned),
        "not_aligned_4h": bucket(not_aligned),
        "note": "Observation only. 4H alignment does not block or promote live Day Trading signals.",
    }


def run() -> None:
    ledger = tracker._load_json(tracker.LEDGER_FILE, tracker._default_ledger())
    candidates = ledger.setdefault("candidates", {})
    exchange = core._get_exchange()
    changed = 0

    for candidate in candidates.values():
        if not isinstance(candidate, dict):
            continue
        if candidate.get("direction_4h") is not None:
            continue
        ccxt_symbol = str(candidate.get("ccxt_symbol") or "")
        if not ccxt_symbol:
            continue
        try:
            df4h = core._fetch_df(exchange, ccxt_symbol, "4h")
            direction4h, metrics4h = core._direction_2h(df4h)
            candidate["direction_4h"] = direction4h or "NEUTRAL"
            candidate["aligned_4h"] = bool(direction4h and direction4h == candidate.get("direction"))
            candidate["rsi_4h"] = metrics4h.get("rsi_2h")
            candidate["extension_atr_4h"] = metrics4h.get("extension_atr_2h")
            candidate["annotated_4h_at"] = core._now()
            changed += 1
        except Exception as exc:
            candidate["direction_4h"] = "ERROR"
            candidate["aligned_4h"] = False
            candidate["4h_error"] = str(exc)[:180]
            candidate["annotated_4h_at"] = core._now()
            changed += 1

    ledger["updated_at"] = core._now()
    tracker._atomic_save(tracker.LEDGER_FILE, ledger)
    tracker._atomic_save(SUMMARY_FILE, _summary(ledger))
    print(f"DAY TRADING 4H SHADOW | annotated {changed} candidates")


if __name__ == "__main__":
    run()
