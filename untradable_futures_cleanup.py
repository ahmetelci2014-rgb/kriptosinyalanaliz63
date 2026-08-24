"""Remove futures that the user cannot actually trade from live Premium state.

Safety purpose
--------------
The live signal bot may see global/public OKX market data that is broader than
what the user's account/region can manually trade. Such symbols must not consume
risk capacity, remain pending, or contaminate performance research.

This module always removes user-confirmed unavailable symbols. If read-only OKX
credentials are configured, it also activates the account-level SWAP allowlist
from /api/v5/account/instruments and removes every live-state symbol that the
account itself does not report as available.

No Telegram messages are sent and no exchange orders are opened/changed/cancelled.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import crypto_universe_guard as guard

VERSION = "UNTRADABLE_FUTURES_CLEANUP_V2_2026_08_24"
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


def _blocked(symbol: Any) -> tuple[bool, str]:
    canonical = guard.canonical_bot_symbol(symbol)
    if not canonical:
        return False, ""
    if canonical in guard.USER_UNTRADABLE_FUTURES_SYMBOLS:
        return True, "USER_INTERFACE_FUTURES_NOT_AVAILABLE"
    if (
        guard.ACCOUNT_ALLOWLIST_ACTIVE
        and canonical not in guard.ACCOUNT_TRADABLE_FUTURES_SYMBOLS
    ):
        return True, "ACCOUNT_FUTURES_NOT_AVAILABLE"
    return False, ""


def _day(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=TR_TZ).strftime("%Y-%m-%d")


def cleanup(*, now_ts: int | None = None) -> Dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())

    # If read-only account credentials exist, this turns account availability
    # into the strongest allowlist. Missing credentials simply leave it OFF.
    account_allowlist = guard.refresh_account_tradable_futures_from_env(force=True)

    removed_open: Dict[str, Dict[str, Any]] = {}
    removed_open_reason: Dict[str, str] = {}
    removed_pending: Dict[str, Dict[str, Any]] = {}
    invalidated_ledger = []

    open_state = _load(OPEN_FILE)
    if open_state:
        for key, signal in list(open_state.items()):
            if not isinstance(signal, dict):
                continue
            blocked, reason = _blocked(signal.get("symbol"))
            if not blocked:
                continue
            removed_open[str(key)] = signal
            removed_open_reason[str(key)] = reason
            open_state.pop(key, None)
        if removed_open:
            _save(OPEN_FILE, open_state)

    pending_state = _load(PENDING_FILE)
    pending = pending_state.get("pending") if isinstance(pending_state.get("pending"), dict) else {}
    for key, row in list(pending.items()):
        signal = row.get("signal") if isinstance(row, dict) and isinstance(row.get("signal"), dict) else {}
        blocked, _ = _blocked(signal.get("symbol"))
        if not blocked:
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
        blocked, reason = _blocked(trade.get("symbol"))
        if not blocked:
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
            "reason": reason,
            "performance_eligible": False,
            "blocked_symbol": guard.canonical_bot_symbol(trade.get("symbol")),
            "account_allowlist_active": bool(guard.ACCOUNT_ALLOWLIST_ACTIVE),
        }
        events = trade.get("events") if isinstance(trade.get("events"), list) else []
        events.append({
            "time": now,
            "event": "INVALID_MARKET_UNTRADABLE",
            "price": None,
            "reason": reason,
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
            "symbol": guard.canonical_bot_symbol(signal.get("symbol")),
            "direction": signal.get("direction"),
            "source": signal.get("source"),
            "reason": removed_open_reason.get(key) or "FUTURES_NOT_AVAILABLE",
            "performance_eligible": False,
            "account_allowlist_active": bool(guard.ACCOUNT_ALLOWLIST_ACTIVE),
        })
        known.add(trade_id)

    state = {
        "version": VERSION,
        "mode": MODE,
        "updated_at": now,
        "blocked_symbols": sorted(guard.USER_UNTRADABLE_FUTURES_SYMBOLS),
        "account_allowlist_active": bool(guard.ACCOUNT_ALLOWLIST_ACTIVE),
        "account_allowlist_refresh_ok": bool(account_allowlist),
        "account_tradable_futures_count": len(guard.ACCOUNT_TRADABLE_FUTURES_SYMBOLS),
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
    print(
        "UNTRADABLE FUTURES CLEANUP:",
        result,
        "| account_allowlist=",
        "ON" if guard.ACCOUNT_ALLOWLIST_ACTIVE else "OFF",
    )


if __name__ == "__main__":
    main()
