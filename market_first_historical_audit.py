"""Historical evidence audit for Market First tuning.

Reads only repository ledgers/reports and prints compact JSON. It does not send
Telegram messages, place orders, or mutate live strategy state.
"""
from __future__ import annotations

import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

VERSION = "MARKET_FIRST_HISTORICAL_AUDIT_V1_2026_09_25"
TRADE_LEDGER = Path("trade_ledger.json")
ENTRY_LEDGER = Path("market_first_entry_plan_ledger.json")


def load(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def sf(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def si(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def canon(value: Any) -> str:
    text = str(value or "").upper().strip()
    aliases = {
        "STOP": "SL",
        "BREAK_EVEN": "BE",
        "TP1+BE": "TP1_SONRASI_BE",
        "TP2+BE": "TP2_SONRASI_BE",
    }
    return aliases.get(text, text)


def net_r(trade: Mapping[str, Any]) -> float | None:
    direct = sf(trade.get("net_r_after_costs"))
    if direct is not None:
        return direct
    gross = sf(trade.get("r_result"))
    return gross


def closed_market_first_trades() -> list[dict[str, Any]]:
    payload = load(TRADE_LEDGER, {})
    raw = payload.get("trades") if isinstance(payload, Mapping) else {}
    rows = []
    if not isinstance(raw, Mapping):
        return rows
    for item in raw.values():
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source") or "").upper()
        if not source.startswith("MARKET_FIRST"):
            continue
        result = canon(item.get("final_result") or item.get("result"))
        if not result:
            continue
        if sf(item.get("r_result")) is None and sf(item.get("net_r_after_costs")) is None:
            continue
        rows.append(dict(item))
    return rows


def metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    values = [net_r(row) for row in rows]
    values = [float(v) for v in values if v is not None]
    results = Counter(canon(row.get("final_result") or row.get("result")) for row in rows)
    positive = [v for v in values if v > 0]
    negative = [v for v in values if v < 0]
    pf = 999.0 if positive and not negative else (
        sum(positive) / abs(sum(negative)) if negative else 0.0
    )
    return {
        "sample": len(values),
        "net_r": round(sum(values), 4),
        "avg_net_r": round(sum(values) / len(values), 4) if values else None,
        "profit_factor": round(pf, 3),
        "positive_rate": round(len(positive) / len(values), 4) if values else 0.0,
        "stop_rate": round(
            (results.get("SL", 0) + results.get("STOP", 0)) / len(rows), 4
        ) if rows else 0.0,
        "tp3_rate": round(results.get("TP3", 0) / len(rows), 4) if rows else 0.0,
        "results": dict(results),
    }


def band(value: float | None, cuts: list[tuple[float, str]], fallback: str) -> str:
    if value is None:
        return "MISSING"
    for upper, label in cuts:
        if value <= upper:
            return label
    return fallback


def nested(mapping: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = mapping.get(key)
    return value if isinstance(value, Mapping) else {}


def trade_confirmations(row: Mapping[str, Any]) -> int | None:
    fg = nested(row, "final_execution_gate")
    if fg.get("confirmations") is not None:
        return si(fg.get("confirmations"))
    de = nested(row, "direction_engine")
    if de.get("confirmations") is not None:
        return si(de.get("confirmations"))
    value = row.get("direction_engine_confirmations")
    return si(value) if value is not None else None


def trade_fresh_micro(row: Mapping[str, Any]) -> bool | None:
    fg = nested(row, "final_execution_gate")
    if "fresh_micro" in fg:
        return bool(fg.get("fresh_micro"))
    de = nested(row, "direction_engine")
    flags = nested(de, "confirmation_flags")
    if "fresh_micro" in flags:
        return bool(flags.get("fresh_micro"))
    return None


def group_metrics(rows: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row))].append(row)
    return {
        key: metrics(value)
        for key, value in sorted(groups.items(), key=lambda item: item[0])
    }


def real_trade_audit(rows: list[dict[str, Any]], now_value: int) -> dict[str, Any]:
    windows = {}
    for name, seconds in (
        ("7d", 7 * 86400),
        ("14d", 14 * 86400),
        ("30d", 30 * 86400),
        ("lifetime", None),
    ):
        if seconds is None:
            subset = rows
        else:
            cutoff = now_value - seconds
            subset = [
                row for row in rows
                if si(row.get("closed_at") or row.get("updated_at")) >= cutoff
            ]
        windows[name] = metrics(subset)

    stops = [
        row for row in rows
        if canon(row.get("final_result") or row.get("result")) in {"SL", "STOP"}
    ]
    thresholds = {}
    for threshold in (0.50, 0.75, 1.00, 1.25, 1.50):
        eligible = [
            row for row in stops
            if (sf(row.get("best_favorable_r"), 0.0) or 0.0) >= threshold
        ]
        thresholds[f"{threshold:.2f}R"] = {
            "stop_count_with_prior_mfe": len(eligible),
            "share_of_stops": round(len(eligible) / len(stops), 4) if stops else 0.0,
        }

    score_groups = group_metrics(
        rows,
        lambda row: band(
            sf(row.get("score")),
            [(87, "<=87"), (91, "88-91"), (93, "92-93")],
            "94+",
        ),
    )
    risk_groups = group_metrics(
        rows,
        lambda row: band(
            sf(row.get("risk_percent")),
            [(0.45, "<=0.45"), (0.70, "0.46-0.70"), (1.00, "0.71-1.00")],
            ">1.00",
        ),
    )
    expected_groups = group_metrics(
        rows,
        lambda row: band(
            sf(row.get("expected_move_percent")),
            [(1.0, "<=1.0"), (2.0, "1.01-2.0"), (3.0, "2.01-3.0")],
            ">3.0",
        ),
    )
    conf_groups = group_metrics(
        rows,
        lambda row: trade_confirmations(row)
        if trade_confirmations(row) is not None else "MISSING",
    )
    fresh_groups = group_metrics(
        rows,
        lambda row: (
            "FRESH" if trade_fresh_micro(row) is True
            else "NOT_FRESH" if trade_fresh_micro(row) is False
            else "MISSING"
        ),
    )
    regime_groups = group_metrics(
        rows, lambda row: str(row.get("market_regime") or "MISSING")
    )
    direction_groups = group_metrics(
        rows, lambda row: str(row.get("direction") or "MISSING").upper()
    )

    stop_mfe = sorted(
        (sf(row.get("best_favorable_r"), 0.0) or 0.0) for row in stops
    )
    rounded_risk = Counter(
        round(float(sf(row.get("risk_percent"))), 3)
        for row in rows
        if sf(row.get("risk_percent")) is not None
    )
    top_risk_values = {}
    for risk_value, count in rounded_risk.most_common(12):
        subset = [
            row for row in rows
            if sf(row.get("risk_percent")) is not None
            and round(float(sf(row.get("risk_percent"))), 3) == risk_value
        ]
        top_risk_values[str(risk_value)] = {"count": count, **metrics(subset)}
    def window_subset(seconds: int) -> list[dict[str, Any]]:
        cutoff = now_value - seconds
        return [
            row for row in rows
            if si(row.get("closed_at") or row.get("updated_at")) >= cutoff
        ]

    recent_profiles = {}
    for label, seconds in (("7d", 7 * 86400), ("14d", 14 * 86400)):
        subset = window_subset(seconds)
        recent_profiles[label] = {
            "by_risk_percent": group_metrics(
                subset,
                lambda row: band(
                    sf(row.get("risk_percent")),
                    [(0.45, "<=0.45"), (0.70, "0.46-0.70"), (1.00, "0.71-1.00")],
                    ">1.00",
                ),
            ),
            "by_score": group_metrics(
                subset,
                lambda row: band(
                    sf(row.get("score")),
                    [(87, "<=87"), (91, "88-91"), (93, "92-93")],
                    "94+",
                ),
            ),
            "by_confirmations": group_metrics(
                subset,
                lambda row: trade_confirmations(row)
                if trade_confirmations(row) is not None else "MISSING",
            ),
        }

    diagnosis_codes = Counter()
    diagnosis_causes = Counter()
    profit_lock_after_close = Counter()
    profit_lock_rows = []
    for row in rows:
        diag = nested(row, "result_diagnostics")
        dx = nested(diag, "diagnosis")
        code = str(dx.get("code") or "").strip()
        cause = str(dx.get("likely_cause") or "").strip()
        if code:
            diagnosis_codes[code] += 1
        if cause:
            diagnosis_causes[cause] += 1
        if canon(row.get("final_result") or row.get("result")) == "PROFIT_LOCK_BE":
            profit_lock_rows.append(row)
            reached = nested(diag, "reached_levels")
            if "TP3" in reached:
                profit_lock_after_close["TP3_AFTER_LOCK"] += 1
            elif "TP2" in reached:
                profit_lock_after_close["TP2_AFTER_LOCK"] += 1
            elif "TP1" in reached:
                profit_lock_after_close["TP1_AFTER_LOCK"] += 1
            elif str(diag.get("status") or "").upper() == "COMPLETED":
                profit_lock_after_close["NO_TARGET_AFTER_LOCK"] += 1
            else:
                profit_lock_after_close["TRACKING_OR_NO_DIAG"] += 1

    return {
        "total": metrics(rows),
        "windows": windows,
        "recent_profiles": recent_profiles,
        "by_direction": direction_groups,
        "by_score": score_groups,
        "by_risk_percent": risk_groups,
        "top_exact_risk_values": top_risk_values,
        "by_expected_move": expected_groups,
        "by_confirmations": conf_groups,
        "by_fresh_micro": fresh_groups,
        "by_market_regime": regime_groups,
        "stop_giveback": {
            "stops": len(stops),
            "median_best_favorable_r_before_stop": round(statistics.median(stop_mfe), 4) if stop_mfe else None,
            "profit_lock_counterfactual": thresholds,
        },
        "result_diagnostics": {
            "diagnosis_codes": dict(diagnosis_codes),
            "likely_causes": dict(diagnosis_causes),
            "profit_lock_be_sample": len(profit_lock_rows),
            "profit_lock_after_close": dict(profit_lock_after_close),
        },
        "field_coverage": {
            "confirmations": sum(trade_confirmations(row) is not None for row in rows),
            "fresh_micro": sum(trade_fresh_micro(row) is not None for row in rows),
            "expected_move_percent": sum(sf(row.get("expected_move_percent")) is not None for row in rows),
            "best_favorable_r": sum(sf(row.get("best_favorable_r")) is not None for row in rows),
        },
    }


def plan_value(item: Mapping[str, Any], key: str) -> Any:
    latest = nested(item, "latest_plan")
    initial = nested(item, "initial")
    if key in latest and latest.get(key) is not None:
        return latest.get(key)
    if key in initial and initial.get(key) is not None:
        return initial.get(key)
    return item.get(key)


def plan_engine(item: Mapping[str, Any]) -> Mapping[str, Any]:
    latest = nested(item, "latest_plan")
    engine = nested(latest, "direction_engine")
    if engine:
        return engine
    return nested(item, "direction_engine")


def plan_confirmations(item: Mapping[str, Any]) -> int | None:
    engine = plan_engine(item)
    if engine.get("confirmations") is not None:
        return si(engine.get("confirmations"))
    value = plan_value(item, "direction_engine_confirmations")
    return si(value) if value is not None else None


def plan_fresh_micro(item: Mapping[str, Any]) -> bool | None:
    engine = plan_engine(item)
    flags = nested(engine, "confirmation_flags")
    if "fresh_micro" in flags:
        return bool(flags.get("fresh_micro"))
    value = plan_value(item, "fresh_micro")
    return bool(value) if value is not None else None


def first_touch(item: Mapping[str, Any]) -> str:
    explicit = str(
        item.get("first_decisive_event")
        or item.get("first_touch")
        or item.get("outcome")
        or ""
    ).upper()
    if "SL_FIRST" in explicit:
        return "SL_FIRST"
    if "TP1_REACHED" in explicit or "TP2_REACHED" in explicit or "TP3_REACHED" in explicit:
        return "TP_FIRST"
    if "TP1_FIRST" in explicit or "TP2_FIRST" in explicit or "TP3_FIRST" in explicit:
        return "TP_FIRST"
    tp = si(item.get("tp1_at"))
    sl = si(item.get("sl_at") or item.get("stop_at"))
    if tp and sl:
        return "TP_FIRST" if tp < sl else "SL_FIRST"
    if tp:
        return "TP_FIRST"
    if sl:
        return "SL_FIRST"
    return "UNKNOWN"


def plan_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    decided = [row for row in rows if first_touch(row) in {"TP_FIRST", "SL_FIRST"}]
    tp = sum(first_touch(row) == "TP_FIRST" for row in decided)
    sl = sum(first_touch(row) == "SL_FIRST" for row in decided)
    return {
        "sample": len(rows),
        "decided": len(decided),
        "tp_first": tp,
        "sl_first": sl,
        "tp_first_rate": round(tp / len(decided), 4) if decided else None,
    }


def plan_group(rows: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row))].append(row)
    return {
        key: plan_metrics(value)
        for key, value in sorted(groups.items(), key=lambda item: item[0])
    }


def entry_plan_audit() -> dict[str, Any]:
    payload = load(ENTRY_LEDGER, {})
    raw = payload.get("episodes") if isinstance(payload, Mapping) else {}
    rows = [
        dict(item) for item in (raw.values() if isinstance(raw, Mapping) else [])
        if isinstance(item, Mapping) and not bool(item.get("exclude_from_clean_prep_stats"))
    ]
    resolved = [row for row in rows if bool(row.get("resolved"))]
    decided = [row for row in resolved if first_touch(row) in {"TP_FIRST", "SL_FIRST"}]

    def htf_aligned(row: Mapping[str, Any]) -> bool:
        direction = str(row.get("direction") or "").upper()
        return (
            str(plan_value(row, "structure_15m") or "").upper() == direction
            and str(plan_value(row, "structure_1h") or "").upper() == direction
        )

    two_fresh = [
        row for row in decided
        if plan_confirmations(row) == 2
        and plan_fresh_micro(row) is True
        and htf_aligned(row)
    ]
    three_plus = [
        row for row in decided
        if plan_confirmations(row) is not None and plan_confirmations(row) >= 3
    ]
    proposed = [
        row for row in decided
        if (
            plan_confirmations(row) is not None
            and (
                plan_confirmations(row) >= 3
                or (
                    plan_confirmations(row) == 2
                    and plan_fresh_micro(row) is True
                    and htf_aligned(row)
                )
            )
        )
    ]

    unsent_tp = [
        row for row in resolved
        if not bool(row.get("entry_signal_sent")) and bool(row.get("tp1_at"))
    ]
    prep_to_tp = []
    for row in unsent_tp:
        start = si(row.get("first_at"))
        end = si(row.get("tp1_at"))
        if start and end >= start:
            prep_to_tp.append((end - start) / 60.0)

    return {
        "clean": plan_metrics(resolved),
        "by_score": plan_group(
            decided,
            lambda row: band(
                sf(plan_value(row, "score")),
                [(79, "<=79"), (83, "80-83"), (87, "84-87"), (91, "88-91"), (93, "92-93")],
                "94+",
            ),
        ),
        "by_risk_percent": plan_group(
            decided,
            lambda row: band(
                sf(plan_value(row, "risk_percent")),
                [(0.45, "<=0.45"), (0.70, "0.46-0.70"), (1.00, "0.71-1.00"), (1.35, "1.01-1.35")],
                ">1.35",
            ),
        ),
        "by_extension_atr_5m": plan_group(
            decided,
            lambda row: band(
                sf(plan_value(row, "extension_atr_5m")),
                [(0.50, "<=0.50"), (0.90, "0.51-0.90"), (1.25, "0.91-1.25"), (1.80, "1.26-1.80")],
                ">1.80",
            ),
        ),
        "by_volume_5m": plan_group(
            decided,
            lambda row: band(
                sf(plan_value(row, "volume_ratio_5m")),
                [(0.50, "<=0.50"), (0.80, "0.51-0.80"), (1.20, "0.81-1.20"), (2.00, "1.21-2.00")],
                ">2.00",
            ),
        ),
        "by_volume_15m": plan_group(
            decided,
            lambda row: band(
                sf(plan_value(row, "volume_ratio_15m")),
                [(0.50, "<=0.50"), (0.80, "0.51-0.80"), (1.20, "0.81-1.20"), (2.00, "1.21-2.00")],
                ">2.00",
            ),
        ),
        "by_confirmations": plan_group(
            decided,
            lambda row: plan_confirmations(row) if plan_confirmations(row) is not None else "MISSING",
        ),
        "by_fresh_micro": plan_group(
            decided,
            lambda row: (
                "FRESH" if plan_fresh_micro(row) is True
                else "NOT_FRESH" if plan_fresh_micro(row) is False
                else "MISSING"
            ),
        ),
        "execution_counterfactual": {
            "three_plus": plan_metrics(three_plus),
            "two_fresh_htf_aligned_only": plan_metrics(two_fresh),
            "three_plus_or_two_fresh_htf": plan_metrics(proposed),
        },
        "missed_timing": {
            "unsent_tp1_or_better": len(unsent_tp),
            "median_minutes_prep_to_tp1": round(statistics.median(prep_to_tp), 2) if prep_to_tp else None,
            "tp1_within_15m": sum(value <= 15 for value in prep_to_tp),
            "tp1_within_30m": sum(value <= 30 for value in prep_to_tp),
            "tp1_within_60m": sum(value <= 60 for value in prep_to_tp),
        },
        "field_coverage": {
            "confirmations": sum(plan_confirmations(row) is not None for row in decided),
            "fresh_micro": sum(plan_fresh_micro(row) is not None for row in decided),
            "structure_15m_1h": sum(
                plan_value(row, "structure_15m") is not None and plan_value(row, "structure_1h") is not None
                for row in decided
            ),
        },
    }


def build() -> dict[str, Any]:
    now_value = int(time.time())
    real_rows = closed_market_first_trades()
    return {
        "version": VERSION,
        "generated_at": now_value,
        "basis": "Repository historical ledgers; observational audit only.",
        "real_trades": real_trade_audit(real_rows, now_value),
        "entry_plans": entry_plan_audit(),
    }


if __name__ == "__main__":
    payload = build()
    print("HISTORICAL_AUDIT_JSON_BEGIN")
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print("HISTORICAL_AUDIT_JSON_END")
