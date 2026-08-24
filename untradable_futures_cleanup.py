"""Remove user-untradable futures from live Premium state.

Safety purpose
--------------
The signal bot uses public OKX market data, while the user executes trades
manually in the OKX interface available to their account/region. Public/global
metadata can occasionally contain a perpetual that the user cannot actually
open. Such a signal must not consume open-risk capacity or become a fake TP/SL
result in performance research.

This module:
- removes blocked symbols from open_signals.json,
- removes them from premium_pending_candidates.json,
- marks matching OPEN ledger trades INVALID_MARKET_UNTRADABLE,
- writes a small audit file,
- sends no Telegram messages and opens/closes no exchange orders.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from crypto_universe_guard import (
    USER_UNTRADABLE_FUTURES_SYMBOLS,
    canonical_bot_symbol,
)

VERSION = "UNTRADABLE_FUTURES_CLEANUP_V1_2026_08_24"
MODE = "STATE_SAFETY_NO_TELEGRAM_NO_ORDERS"
OPEN_FILE = "open_signals.json"
LEDGER_FILE = "trade_ledger.json"
PENDING_FILE = "premium_pending_candidates.json"
AUDIT_FILE = "invalid_market_signals.json"
TR_TZ = timezone(timedelta(hours=3))


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        with open(tmp, "r", encoding="utf-8") as verify:
            checked = json.load(verify)
        if not isinstance(checked, dict):
            raise ValueError("JSON root is not object")
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _blocked(symbol: Any) -> bool:
    return canonical_bot_symbol(symbol) in USER_UNTRADABLE_FUTURES_SYMBOLS


def _day(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=TR_TZ).strftime("%Y-%m-%d")


def cleanup(*, now_ts: int | None = None) -> Dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    removed_open: Dict[str, Dict[str, Any]] = {}
    removed_pending: Dict[str, Dict[str, Any]] = {}
    invalidated_ledger = []

    open_state = _load(OPEN_FILE)
    if open_state:
        for key, signal in list(open_state.items()):
            if not isinstance(signal, dict) or not _blocked(signal.get("symbol")):
                continue
            removed_open[str(key)] = signal
            open_state.pop(key, None)
        if removed_open:
            _save(OPEN_FILE, open_state)

    pending_state = _load(PENDING_FILE)
    pending = pending_state.get("pending") if isinstance(pending_state.get("pending"), dict) else {}
    for key, row in list(pending.items()):
        signal = row.get("signal") if isinstance(row, dict) and isinstance(row.get("signal"), dict) else {}
        if not _blocked(signal.get("symbol")):
            continue
        removed_pending[str(key)] = row
        pending.pop(key, None)
    if removed_pending:
        pending_state["pending"] = pending
        pending_state["last_update"] = now
        _save(PENDING_FILE, pending_state)

    ledger = _load(LEDGER_FILE)
    trades = ledger.get("trades") if isinstance(ledger.get("trades"), dict) else {}
    ledger_changed = False
    for trade_id, trade in trades.items():
        if not isinstance(trade, dict):
            continue
        if not _blocked(trade.get("symbol")):
            continue
        if str(trade.get("status") or "").upper() not in {"OPEN", ""}:
            continue

        trade["status"] = "INVALID"
        trade["final_result"] = "INVALID_MARKET_UNTRADABLE"
        trade["r_result"] = None
        trade["exit_price"] = None
        trade["closed_at"] = now
        trade["closed_day"] = _day(now)
        trade["invalid_market"] = {
            "version": VERSION,
            "at": now,
            "reason": "USER_INTERFACE_FUTURES_NOT_AVAILABLE",
            "performance_eligible": False,
            "blocked_symbol": canonical_bot_symbol(trade.get("symbol")),
        }
        events = trade.get("events") if isinstance(trade.get("events"), list) else []
        events.append({
            "time": now,
            "event": "INVALID_MARKET_UNTRADABLE",
            "price": None,
        })
        trade["events"] = events
        invalidated_ledger.append(str(trade_id))
        ledger_changed = True

    if ledger_changed:
        ledger["last_update"] = now
        _save(LEDGER_FILE, ledger)

    audit = _load(AUDIT_FILE)
    rows = audit.get("records") if isinstance(audit.get("records"), list) else []
    known = {
        str(row.get("trade_id") or "")
        for row in rows
        if isinstance(row, dict)
    }
    for key, signal in removed_open.items():
        trade_id = str(signal.get("trade_id") or key)
        if trade_id in known:
            continue
        rows.append({
            "at": now,
            "trade_id": trade_id,
            "symbol": canonical_bot_symbol(signal.get("symbol")),
            "direction": signal.get("direction"),
            "source": signal.get("source"),
            "reason": "USER_INTERFACE_FUTURES_NOT_AVAILABLE",
            "performance_eligible": False,
        })
        known.add(trade_id)

    state = {
        "version": VERSION,
        "mode": MODE,
        "updated_at": now,
        "blocked_symbols": sorted(USER_UNTRADABLE_FUTURES_SYMBOLS),
        "records": rows[-500:],
        "last_run": {
            "removed_open": sorted(removed_open),
            "removed_pending": sorted(removed_pending),
            "invalidated_ledger": invalidated_ledger,
        },
    }
    _save(AUDIT_FILE, state)
    return state["last_run"]


def main() -> None:
    result = cleanup()
    print("UNTRADABLE FUTURES CLEANUP:", result)


if __name__ == "__main__":
    main()
