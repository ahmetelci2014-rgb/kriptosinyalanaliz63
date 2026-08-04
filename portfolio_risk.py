"""
Beş bot için ortak portföy risk ve sinyal çakışma denetimi.

V3 değişikliği:
- Aynı coin çakışmaları eskisi gibi engellenir.
- Aynı yön risk ağırlığı 4.0'ı aşacaksa yeni sinyal engellenir.
- Toplam açık risk ağırlığı 8.0'ı aşacaksa yeni sinyal engellenir.
- TP1 görülmüş açık işlemler 0.5 risk ağırlığıyla sayılır.

Bu modül emir açmaz. State dosyalarını yalnızca okur.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_STATE_SOURCES = {
    "MAIN_MTF": {
        "filename": "open_signals.json",
        "containers": [None],
    },
    "SCALP": {
        "filename": "scalp_radar_state.json",
        "containers": ["open_scalp_signals"],
    },
    "PUMP_DUMP": {
        "filename": "pump_radar_state.json",
        "containers": ["open_signals", "open_pump_signals"],
    },
    "SWING": {
        "filename": "swing_radar_state.json",
        "containers": ["open_swing_signals"],
    },
    "NEW_LISTING": {
        "filename": "new_listing_performance_ledger.json",
        "containers": ["records"],
        "required_record_type": "CONFIRMED_TRADE",
    },
}

DEFAULT_MAX_DIRECTION_RISK = 4.0
DEFAULT_MAX_TOTAL_RISK = 8.0


def normalize_symbol(symbol: Any) -> str:
    value = str(symbol or "").upper().strip()
    if not value:
        return ""

    if "/" in value:
        base, remainder = value.split("/", 1)
        quote = remainder.split(":", 1)[0]
        value = base + quote

    return (
        value.replace("-", "")
        .replace("_", "")
        .replace(":", "")
        .replace("/", "")
        .replace(" ", "")
    )


def safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json_safely(filename: str) -> Dict[str, Any]:
    if not filename or not os.path.exists(filename):
        return {}

    try:
        with open(filename, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        print(filename, "portföy risk dosyası okuma hatası:", exc)
        return {}


def signal_is_open(signal: Any) -> bool:
    if not isinstance(signal, dict):
        return False
    if bool(signal.get("closed", False)) or bool(signal.get("tp3_hit", False)):
        return False

    status = str(signal.get("status", "")).upper()
    if status in {"CLOSED", "EXPIRED", "CANCELLED", "FINAL"}:
        return False

    return bool(signal.get("symbol") and signal.get("direction"))


def signal_risk_weight(signal: Dict[str, Any]) -> float:
    return 0.5 if bool(signal.get("tp1_hit", False)) else 1.0


def extract_container_signals(
    loaded: Dict[str, Any],
    containers: Iterable[Optional[str]],
    required_record_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen_keys = set()

    for container_name in containers:
        candidate_container = loaded if container_name is None else loaded.get(container_name, {})
        if not isinstance(candidate_container, dict):
            continue

        for key, signal in candidate_container.items():
            if not isinstance(signal, dict):
                continue

            if required_record_type:
                record_type = str(signal.get("record_type") or "").upper()
                if record_type != str(required_record_type).upper():
                    continue

            if not signal_is_open(signal):
                continue

            identity = (
                str(key),
                normalize_symbol(signal.get("symbol")),
                str(signal.get("direction", "")).upper(),
            )
            if identity in seen_keys:
                continue

            seen_keys.add(identity)
            results.append(signal)

    return results


def collect_open_portfolio(state_sources: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    sources = state_sources if isinstance(state_sources, dict) else DEFAULT_STATE_SOURCES
    records: List[Dict[str, Any]] = []

    for bot_name, source in sources.items():
        loaded = load_json_safely(source.get("filename"))
        for signal in extract_container_signals(
            loaded,
            source.get("containers", [None]),
            required_record_type=source.get("required_record_type"),
        ):
            records.append({
                "bot": str(bot_name),
                "symbol": normalize_symbol(signal.get("symbol")),
                "direction": str(signal.get("direction", "")).upper(),
                "source": (
                    signal.get("source")
                    or signal.get("alert_type")
                    or signal.get("record_type")
                ),
                "entry": safe_float(signal.get("entry"), None),
                "risk_percent": safe_float(
                    signal.get("risk_percent")
                    if signal.get("risk_percent") is not None
                    else signal.get("stop_percent"),
                    None,
                ),
                "tp1_hit": bool(signal.get("tp1_hit", False)),
                "risk_weight": signal_risk_weight(signal),
                "opened_at": int(
                    safe_float(
                        signal.get("opened_at")
                        if signal.get("opened_at") is not None
                        else signal.get("sent_at"),
                        0,
                    )
                    or 0
                ),
            })

    return records


def evaluate_portfolio_risk(
    symbol: str,
    direction: str,
    source_bot: str,
    state_sources: Optional[Dict[str, Any]] = None,
    max_direction_risk: float = DEFAULT_MAX_DIRECTION_RISK,
    max_total_risk: float = DEFAULT_MAX_TOTAL_RISK,
) -> Dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    normalized_direction = str(direction or "").upper()
    normalized_bot = str(source_bot or "UNKNOWN").upper()

    open_records = collect_open_portfolio(state_sources=state_sources)
    same_symbol = [item for item in open_records if item["symbol"] == normalized_symbol]
    same_symbol_same_direction = [
        item for item in same_symbol if item["direction"] == normalized_direction
    ]
    same_symbol_opposite = [
        item for item in same_symbol
        if item["direction"] and item["direction"] != normalized_direction
    ]

    total_risk_before = round(sum(item["risk_weight"] for item in open_records), 2)
    direction_risk_before = round(
        sum(
            item["risk_weight"]
            for item in open_records
            if item["direction"] == normalized_direction
        ),
        2,
    )
    total_risk_after = round(total_risk_before + 1.0, 2)
    direction_risk_after = round(direction_risk_before + 1.0, 2)

    hard_block = False
    block_code = None
    block_reason = None

    if same_symbol_opposite:
        hard_block = True
        block_code = "SAME_COIN_OPPOSITE_DIRECTION"
        bots = ", ".join(sorted({item["bot"] for item in same_symbol_opposite}))
        block_reason = f"{normalized_symbol} başka botta ters yönde açık: {bots}."
    elif same_symbol_same_direction:
        hard_block = True
        block_code = "SAME_COIN_SAME_DIRECTION"
        bots = ", ".join(sorted({item["bot"] for item in same_symbol_same_direction}))
        block_reason = (
            f"{normalized_symbol} aynı yönde başka açık sinyalle çakışıyor: {bots}."
        )
    elif direction_risk_after > float(max_direction_risk):
        hard_block = True
        block_code = "DIRECTION_RISK_LIMIT"
        block_reason = (
            f"{normalized_direction} yön ağırlığı {direction_risk_after:.1f}/"
            f"{float(max_direction_risk):.1f} sınırını aşacak."
        )
    elif total_risk_after > float(max_total_risk):
        hard_block = True
        block_code = "TOTAL_RISK_LIMIT"
        block_reason = (
            f"Toplam açık risk ağırlığı {total_risk_after:.1f}/"
            f"{float(max_total_risk):.1f} sınırını aşacak."
        )

    warnings: List[str] = []
    if not hard_block and direction_risk_after == float(max_direction_risk):
        warnings.append(
            f"{normalized_direction} yön ağırlığı üst sınıra ulaştı: "
            f"{direction_risk_after:.1f}/{float(max_direction_risk):.1f}"
        )
    if not hard_block and total_risk_after == float(max_total_risk):
        warnings.append(
            f"toplam açık risk ağırlığı üst sınıra ulaştı: "
            f"{total_risk_after:.1f}/{float(max_total_risk):.1f}"
        )

    return {
        "version": "PORTFOLIO_RISK_V3_HARD_CAP",
        "checked_at": int(time.time()),
        "candidate": {
            "bot": normalized_bot,
            "symbol": normalized_symbol,
            "direction": normalized_direction,
        },
        "hard_block": hard_block,
        "block_code": block_code,
        "block_reason": block_reason,
        "warnings": warnings,
        "has_soft_warning": bool(warnings),
        "open_signal_count": len(open_records),
        "total_risk_before": total_risk_before,
        "total_risk_after": total_risk_after,
        "direction_risk_before": direction_risk_before,
        "direction_risk_after": direction_risk_after,
        "same_symbol_records": same_symbol,
    }


def format_portfolio_note(result: Any) -> str:
    if not isinstance(result, dict):
        return ""

    if result.get("hard_block"):
        return "⛔ Portföy Risk Engeli: " + str(
            result.get("block_reason") or "Açık risk sınırı aşılıyor."
        )

    warnings = result.get("warnings") or []
    if not warnings:
        return ""

    return "⚠️ Portföy Yoğunluk Uyarısı: " + "; ".join(warnings) + "."
