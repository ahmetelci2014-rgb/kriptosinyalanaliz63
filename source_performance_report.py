"""Source-level Premium performance audit.

Reads the canonical ``trade_ledger.json`` and attaches a source breakdown to
``profit_mode_report.json``. It changes no live gate and sends no Telegram.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

import profitability_engine as profit

VERSION = "SOURCE_PERFORMANCE_REPORT_V1_2026_08_25"

DEFAULT_LIVE_SOURCES = (
    "15M_ENTRY",
    "EARLY_BREAKOUT_ENTRY",
    "BIG_MOVE_ENTRY",
    "REGIME_TRANSITION_ENTRY",
    "TREND_CONTINUATION_ENTRY",
    "YOUNG_COIN_ENTRY",
    "NEW_COIN_ENTRY",
)

WINDOWS_SECONDS = {
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
    "lifetime": None,
}


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    return profit.sf(value, default)


def canonical_source(value: Any) -> str:
    return str(value or "UNKNOWN").upper().strip() or "UNKNOWN"


def is_current_live_source(source: str) -> bool:
    source = canonical_source(source)
    return source in DEFAULT_LIVE_SOURCES or source.endswith("_ENTRY")


def _closed_trades(ledger_path: str) -> List[Dict[str, Any]]:
    data = profit.load(ledger_path, {})
    trades = data.get("trades") or {}
    if not isinstance(trades, dict):
        return []
    result = []
    for trade in trades.values():
        if not isinstance(trade, dict):
            continue
        if str(trade.get("status") or "").upper() != "CLOSED":
            continue
        if _sf(trade.get("r_result")) is None:
            continue
        result.append(trade)
    return result


def _net_r(trade: Dict[str, Any]) -> Optional[float]:
    stored = _sf(trade.get("net_r_after_costs"))
    if stored is not None:
        return stored
    return profit.net_r(
        trade.get("r_result"),
        trade.get("entry"),
        trade.get("sl"),
    )


def _gross_r(trade: Dict[str, Any]) -> Optional[float]:
    return _sf(trade.get("r_result"))


def _status_for_metrics(metrics: Dict[str, Any]) -> str:
    sample = int(metrics.get("sample") or 0)
    if sample < profit.MIN_SAMPLE:
        return "INSUFFICIENT_SAMPLE"
    comparable = {
        "sample": sample,
        "avg_net_r_after_costs": metrics.get("avg_net_r_after_costs"),
        "profit_factor_after_costs": metrics.get("profit_factor_after_costs"),
        "stop_rate_percent": metrics.get("stop_rate_percent"),
    }
    return "POSITIVE_EDGE" if profit.passes(comparable) else "EDGE_NOT_PROVEN"


def metrics(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(trades)
    net_values: List[float] = []
    gross_values: List[float] = []
    results = Counter()
    directions = Counter()

    for trade in rows:
        net = _net_r(trade)
        gross = _gross_r(trade)
        if net is None:
            continue
        net_values.append(float(net))
        if gross is not None:
            gross_values.append(float(gross))
        results[canonical_source(trade.get("final_result"))] += 1
        directions[canonical_source(trade.get("direction"))] += 1

    positive = [value for value in net_values if value > 0]
    negative = [value for value in net_values if value < 0]
    net_total = sum(net_values)
    gross_total = sum(gross_values)
    sample = len(net_values)
    pf = (
        sum(positive) / abs(sum(negative))
        if negative
        else (999.0 if positive else 0.0)
    )
    stop_count = int(results.get("SL", 0) + results.get("STOP", 0))
    be_count = int(
        results.get("BE", 0)
        + results.get("TP1_SONRASI_BE", 0)
        + results.get("TP2_SONRASI_BE", 0)
    )
    tp3_count = int(results.get("TP3", 0))

    out: Dict[str, Any] = {
        "sample": sample,
        "gross_r": round(gross_total, 4),
        "net_r_after_costs": round(net_total, 4),
        "avg_net_r_after_costs": round(net_total / sample, 4) if sample else None,
        "profit_factor_after_costs": round(pf, 3),
        "positive_rate_percent": round(len(positive) / sample * 100.0, 2) if sample else 0.0,
        "stop_rate_percent": round(stop_count / sample * 100.0, 2) if sample else 0.0,
        "tp3_rate_percent": round(tp3_count / sample * 100.0, 2) if sample else 0.0,
        "be_family_rate_percent": round(be_count / sample * 100.0, 2) if sample else 0.0,
        "result_counts": dict(results),
        "direction_counts": {
            "LONG": int(directions.get("LONG", 0)),
            "SHORT": int(directions.get("SHORT", 0)),
        },
    }
    out["evidence_status"] = _status_for_metrics(out)
    return out


def _window_filter(
    trades: Iterable[Dict[str, Any]],
    now_value: int,
    window_seconds: Optional[int],
) -> List[Dict[str, Any]]:
    rows = list(trades)
    if window_seconds is None:
        return rows
    cutoff = int(now_value) - int(window_seconds)
    result = []
    for trade in rows:
        closed_at = int(_sf(trade.get("closed_at"), 0) or 0)
        if closed_at >= cutoff:
            result.append(trade)
    return result


def generate(
    ledger_path: str = "trade_ledger.json",
    *,
    now_value: Optional[int] = None,
) -> Dict[str, Any]:
    now_value = int(now_value if now_value is not None else time.time())
    closed = _closed_trades(ledger_path)
    sources = sorted(
        set(DEFAULT_LIVE_SOURCES)
        | {
            canonical_source(trade.get("source"))
            for trade in closed
            if is_current_live_source(canonical_source(trade.get("source")))
        }
    )

    windows: Dict[str, Any] = {}
    for window_name, seconds in WINDOWS_SECONDS.items():
        in_window = _window_filter(closed, now_value, seconds)
        live_rows = [
            trade
            for trade in in_window
            if is_current_live_source(canonical_source(trade.get("source")))
        ]
        by_source = {}
        for source in sources:
            by_source[source] = metrics(
                trade
                for trade in in_window
                if canonical_source(trade.get("source")) == source
            )
        windows[window_name] = {
            "combined_live": metrics(live_rows),
            "sources": by_source,
        }

    return {
        "version": VERSION,
        "generated_at": now_value,
        "basis": "CLOSED trade_ledger trades; one final result per trade_id; execution-cost adjusted R",
        "note": (
            "This is reporting only. It does not change Premium live admission. "
            "Evidence status uses the same minimum sample/avg R/profit-factor/stop-rate thresholds as Profit Mode."
        ),
        "thresholds": {
            "min_sample": profit.MIN_SAMPLE,
            "min_avg_net_r": profit.MIN_AVG,
            "min_profit_factor": profit.MIN_PF,
            "max_stop_rate_percent": profit.MAX_STOP,
        },
        "sources": sources,
        "windows": windows,
    }


def attach_to_profit_report(
    ledger_path: str = "trade_ledger.json",
    report_path: str = profit.REPORT_FILE,
) -> Dict[str, Any]:
    breakdown = generate(ledger_path)
    report = profit.load(report_path, {})
    if not isinstance(report, dict):
        report = {}
    report["live_source_breakdown"] = breakdown
    profit.save(report_path, report)
    return breakdown
