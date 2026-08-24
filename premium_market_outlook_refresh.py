"""Keep Market Outlook fresh inside the Premium workflow.

The separate scheduled Market Outlook workflow remains available, but Premium
must not depend on it being on time. Before scanning, this helper refreshes the
shared market_outlook_state.json when the latest snapshot is stale.

No Telegram message is sent by this helper.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

MAX_AGE_SECONDS = 20 * 60
STATE_FILE = "market_outlook_state.json"


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def latest_snapshot_ts(path: str = STATE_FILE) -> int:
    state = _load(path)
    rows = state.get("snapshots") if isinstance(state.get("snapshots"), list) else []
    if rows and isinstance(rows[-1], dict):
        try:
            return int(rows[-1].get("ts") or 0)
        except Exception:
            return 0
    try:
        return int(state.get("updated_at") or 0)
    except Exception:
        return 0


def ensure_fresh(
    *,
    state_file: str = STATE_FILE,
    now_ts: Optional[int] = None,
    max_age_seconds: int = MAX_AGE_SECONDS,
    exchange: Any = None,
) -> Dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    before = latest_snapshot_ts(state_file)
    age = max(0, now - before) if before > 0 else None
    if age is not None and age <= int(max_age_seconds):
        return {
            "ok": True,
            "refreshed": False,
            "reason": "FRESH",
            "age_seconds": age,
            "snapshot_ts": before,
        }

    try:
        import market_outlook_engine as outlook

        if exchange is None:
            import ccxt

            exchange = ccxt.okx({
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
        result = outlook.run(
            exchange,
            state_file=state_file,
            token=None,
            chat_id=None,
            current_ts=now,
            allow_telegram=False,
        )
        snapshot = (result or {}).get("snapshot") or {}
        after = int(snapshot.get("ts") or latest_snapshot_ts(state_file) or 0)
        after_age = max(0, now - after) if after > 0 else None
        ok = after > 0 and after_age is not None and after_age <= int(max_age_seconds)
        return {
            "ok": bool(ok),
            "refreshed": True,
            "reason": "REFRESHED" if ok else "REFRESH_DID_NOT_CREATE_FRESH_SNAPSHOT",
            "age_seconds": after_age,
            "snapshot_ts": after,
        }
    except Exception as exc:
        return {
            "ok": False,
            "refreshed": False,
            "reason": "REFRESH_ERROR",
            "error": str(exc),
            "age_seconds": age,
            "snapshot_ts": before,
        }
