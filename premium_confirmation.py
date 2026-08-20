from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from typing import Any, Callable, Dict, Optional, Tuple

VERSION = "PREMIUM_PENDING_CONFIRMATION_V1_2026_08_20"
DEFAULT_STATE_FILE = "premium_pending_candidates.json"
DEFAULT_MAX_AGE_SECONDS = 45 * 60
WAIT_REASONS = {
    "CONFIRMATION_NOT_STARTED",
    "PRICE_HAS_NOT_CONFIRMED_ENOUGH",
}
LOCKED_LEVEL_FIELDS = {
    "entry",
    "tp1",
    "tp2",
    "tp3",
    "sl",
    "risk_percent",
    "rr_tp1",
    "rr_tp2",
    "rr_tp3",
    "ideal_entry",
}


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(path: str, data: Dict[str, Any]) -> bool:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".premium_pending.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        with open(temp_path, "r", encoding="utf-8") as handle:
            checked = json.load(handle)
        if not isinstance(checked, dict):
            raise ValueError("pending state root")
        os.replace(temp_path, path)
        temp_path = None
        return True
    except Exception as exc:
        print("Premium pending state write:", type(exc).__name__, exc)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _candidate_key(signal: Dict[str, Any]) -> str:
    return "|".join(
        [
            str(signal.get("symbol") or "").upper(),
            str(signal.get("direction") or "").upper(),
            str(signal.get("source") or "15M_ENTRY").upper(),
        ]
    )


def _merge_anchor(anchor: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    approved = copy.deepcopy(anchor)
    for key, value in current.items():
        if key in LOCKED_LEVEL_FIELDS:
            continue
        approved[key] = copy.deepcopy(value)
    return approved


class PendingConfirmationGate:
    def __init__(
        self,
        premium_gate: Any,
        state_file: str = DEFAULT_STATE_FILE,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.premium_gate = premium_gate
        self.state_file = state_file
        self.max_age_seconds = int(max_age_seconds)
        self.now_fn = now_fn

    def _now(self) -> int:
        return int(self.now_fn())

    def _state(self) -> Dict[str, Any]:
        state = _load(self.state_file)
        pending = state.get("pending")
        if not isinstance(pending, dict):
            pending = {}
        state = {
            "version": VERSION,
            "pending": pending,
            "last_update": int(state.get("last_update") or 0),
        }
        self._cleanup(state)
        return state

    def _cleanup(self, state: Dict[str, Any]) -> None:
        now = self._now()
        pending = state.setdefault("pending", {})
        expired = []
        for key, record in pending.items():
            if not isinstance(record, dict):
                expired.append(key)
                continue
            created_at = int(record.get("created_at") or 0)
            if created_at <= 0 or now - created_at > self.max_age_seconds:
                expired.append(key)
        for key in expired:
            pending.pop(key, None)

    def _save_state(self, state: Dict[str, Any]) -> None:
        state["version"] = VERSION
        state["last_update"] = self._now()
        _save(self.state_file, state)

    def _evidence_allows(self, signal: Dict[str, Any]) -> bool:
        direction = str(signal.get("direction") or "").upper()
        profiles = getattr(self.premium_gate, "profiles", {})
        evidence = profiles.get(direction) if isinstance(profiles, dict) else None
        if isinstance(evidence, dict):
            return bool(evidence.get("live_allowed"))
        return True

    def _store_pending(
        self,
        state: Dict[str, Any],
        signal: Dict[str, Any],
        current_price: Any,
    ) -> None:
        now = self._now()
        key = _candidate_key(signal)
        state.setdefault("pending", {})[key] = {
            "created_at": now,
            "last_seen_at": now,
            "initial_price": current_price,
            "signal": copy.deepcopy(signal),
        }
        self._save_state(state)

    def _annotate_confirmation(
        self,
        signal: Dict[str, Any],
        record: Optional[Dict[str, Any]],
        status: str,
    ) -> None:
        meta = {
            "version": VERSION,
            "status": status,
            "confirmed_at": self._now() if status == "CONFIRMED" else None,
        }
        if isinstance(record, dict):
            meta["candidate_created_at"] = int(record.get("created_at") or 0)
            anchor = record.get("signal") or {}
            if isinstance(anchor, dict):
                meta["anchor_entry"] = anchor.get("entry")
        signal["premium_confirmation"] = meta

    def evaluate(
        self,
        signal: Dict[str, Any],
        current_price: Any,
        base_validator: Callable[[Dict[str, Any], Any], Tuple[bool, str]],
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        key = _candidate_key(signal)
        state = self._state()
        pending = state.setdefault("pending", {})

        current_ok, current_reason = base_validator(signal, current_price)
        if not current_ok:
            if key in pending:
                pending.pop(key, None)
                self._save_state(state)
            return False, current_reason, None

        record = pending.get(key)
        if isinstance(record, dict):
            anchor = record.get("signal")
            if isinstance(anchor, dict):
                anchored_signal = _merge_anchor(anchor, signal)
                anchor_ok, _ = base_validator(anchored_signal, current_price)
                if anchor_ok:
                    result = self.premium_gate.evaluate(anchored_signal, current_price)
                    reason = str(result.get("reason") or "")
                    if result.get("ok"):
                        signal.clear()
                        signal.update(anchored_signal)
                        self._annotate_confirmation(signal, record, "CONFIRMED")
                        pending.pop(key, None)
                        self._save_state(state)
                        return True, "Premium V2 teyitli", result
                    if reason in WAIT_REASONS:
                        record["last_seen_at"] = self._now()
                        record["latest_score"] = signal.get("score")
                        pending[key] = record
                        self._save_state(state)
                        return False, "Premium V2: teyit bekleniyor", result
                    self.premium_gate.reject(anchored_signal, current_price, result)
                    pending.pop(key, None)
                    self._save_state(state)
                    return False, "Profit V2: " + reason, result

            # İlk sabit aday artık giriş/geçlik kontrolünü geçmiyorsa,
            # mevcut kurulum hâlâ geçerliyse güncel adayla yeni teyit döngüsü başlar.
            pending.pop(key, None)
            self._save_state(state)

        result = self.premium_gate.evaluate(signal, current_price)
        reason = str(result.get("reason") or "")
        if result.get("ok"):
            self._annotate_confirmation(signal, None, "DIRECT")
            return True, "Premium V2 uygun", result

        if reason in WAIT_REASONS and self._evidence_allows(signal):
            self._store_pending(state, signal, current_price)
            return False, "Premium V2: teyit bekleniyor", result

        self.premium_gate.reject(signal, current_price, result)
        return False, "Profit V2: " + reason, result

    def pending_count(self) -> int:
        state = self._state()
        return len(state.get("pending") or {})
