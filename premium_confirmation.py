from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from typing import Any, Callable, Dict, Optional, Tuple

VERSION = "PREMIUM_PENDING_CONFIRMATION_V2_2026_08_20"
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


def _normalize_anchor_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    anchor = copy.deepcopy(signal)
    # Premium geçmiş profili 15M_ENTRY işlemlerinden oluşur. Ana main.py,
    # market guard tersse TRADE sinyalini geçici olarak RADAR'a düşürür.
    # Bekleyen aday kaydında orijinal TRADE sınıfını koruruz; sonraki turda
    # güncel market guard yeniden uygulanır.
    if str(anchor.get("source") or "").upper() == "15M_ENTRY":
        anchor["signal_class"] = "TRADE"
    return anchor


def _normalize_candle_time(value: Any) -> int:
    try:
        ts = int(float(value))
    except Exception:
        return 0
    # ccxt dataframe zamanları ms, bazı test/yardımcı yapılar saniye kullanır.
    if ts > 10_000_000_000:
        ts //= 1000
    return ts


def _iter_candles(frame: Any):
    if frame is None:
        return []

    if hasattr(frame, "iterrows"):
        rows = []
        try:
            for _, row in frame.iterrows():
                rows.append(row)
        except Exception:
            return []
        return rows

    if isinstance(frame, (list, tuple)):
        return list(frame)

    return []


def _row_value(row: Any, key: str) -> Any:
    try:
        if hasattr(row, "get"):
            return row.get(key)
        return row[key]
    except Exception:
        return None


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
        loaded = _load(self.state_file)
        pending = loaded.get("pending")
        if not isinstance(pending, dict):
            pending = {}

        state = {
            "version": VERSION,
            "pending": pending,
            "last_update": int(loaded.get("last_update") or 0),
        }

        if self._cleanup(state):
            state["last_update"] = self._now()
            _save(self.state_file, state)

        return state

    def _cleanup(self, state: Dict[str, Any]) -> bool:
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

        return bool(expired)

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
        previous = state.setdefault("pending", {}).get(key)

        if isinstance(previous, dict):
            created_at = int(previous.get("created_at") or now)
        else:
            created_at = now

        state["pending"][key] = {
            "created_at": created_at,
            "last_seen_at": now,
            "initial_price": (
                previous.get("initial_price")
                if isinstance(previous, dict)
                else current_price
            ),
            "signal": _normalize_anchor_signal(signal),
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

    def _remove_key(self, state: Dict[str, Any], key: str) -> None:
        if key in state.setdefault("pending", {}):
            state["pending"].pop(key, None)
            self._save_state(state)

    def _pending_record_for_symbol(
        self,
        state: Dict[str, Any],
        symbol: str,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        wanted = str(symbol or "").upper()
        matches = []

        for key, record in state.get("pending", {}).items():
            if not isinstance(record, dict):
                continue
            anchor = record.get("signal")
            if not isinstance(anchor, dict):
                continue
            if str(anchor.get("symbol") or "").upper() != wanted:
                continue
            matches.append((key, record))

        if not matches:
            return None, None

        matches.sort(
            key=lambda item: int(item[1].get("created_at") or 0),
            reverse=True,
        )
        return matches[0]

    def _candidate_invalidated_by_candles(
        self,
        record: Dict[str, Any],
        df15m: Any,
    ) -> Optional[str]:
        anchor = record.get("signal") or {}
        if not isinstance(anchor, dict):
            return "ANCHOR_MISSING"

        direction = str(anchor.get("direction") or "").upper()

        try:
            sl = float(anchor.get("sl"))
            tp1 = float(anchor.get("tp1"))
        except Exception:
            return "LEVEL_MISSING"

        created_at = int(record.get("created_at") or 0)
        relevant = []

        for row in _iter_candles(df15m):
            candle_time = _normalize_candle_time(_row_value(row, "time"))
            if candle_time <= 0:
                continue
            # 15M mum, aday oluşturulmadan önce başladıysa ama adaydan sonra
            # devam etmiş olabilir; son 15 dakikalık pencereyi güvenli kapsa.
            if candle_time + 15 * 60 < created_at:
                continue
            relevant.append(row)

        if not relevant:
            return None

        highs = []
        lows = []

        for row in relevant:
            try:
                highs.append(float(_row_value(row, "high")))
                lows.append(float(_row_value(row, "low")))
            except Exception:
                continue

        if not highs or not lows:
            return None

        if direction == "LONG":
            if min(lows) <= sl:
                return "SL_SEEN_BEFORE_CONFIRMATION"
            if max(highs) >= tp1:
                return "TP1_SEEN_BEFORE_CONFIRMATION"

        elif direction == "SHORT":
            if max(highs) >= sl:
                return "SL_SEEN_BEFORE_CONFIRMATION"
            if min(lows) <= tp1:
                return "TP1_SEEN_BEFORE_CONFIRMATION"

        return None

    def fallback_signal(
        self,
        symbol: str,
        df15m: Any,
        df1h: Any,
        df4h: Any,
        strategy_module: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        Fresh 15M reversal candle no longer reproduces the setup, but a candidate
        is still inside its 45-minute confirmation window. Reinsert the anchored
        candidate into main.py so current price + market guard + portfolio risk
        are evaluated again on every run.
        """
        state = self._state()
        key, record = self._pending_record_for_symbol(state, symbol)

        if not key or not isinstance(record, dict):
            return None

        anchor = record.get("signal")
        if not isinstance(anchor, dict):
            self._remove_key(state, key)
            return None

        direction = str(anchor.get("direction") or "").upper()

        try:
            trend, trend_reason, trend_info = strategy_module.get_4h_trend(df4h)
            confirm, confirm_reason, confirm_info = strategy_module.get_1h_confirm(df1h)
            supported = strategy_module.trend_supports_direction(
                direction,
                trend,
                confirm,
                strict=True,
            )
        except Exception as exc:
            print("Premium pending trend recheck:", type(exc).__name__, exc)
            return None

        if not supported:
            print(symbol, "Premium bekleyen aday trend bozulduğu için iptal.")
            self._remove_key(state, key)
            return None

        invalidation = self._candidate_invalidated_by_candles(record, df15m)
        if invalidation:
            print(symbol, "Premium bekleyen aday iptal:", invalidation)
            self._remove_key(state, key)
            return None

        signal = _normalize_anchor_signal(anchor)
        signal["trend_reason"] = trend_reason
        signal["confirm_reason"] = confirm_reason

        if isinstance(trend_info, dict):
            signal["adx_4h"] = trend_info.get("adx_4h", signal.get("adx_4h"))
        if isinstance(confirm_info, dict):
            signal["adx_1h"] = confirm_info.get("adx_1h", signal.get("adx_1h"))

        signal["premium_pending_fallback"] = True
        signal["premium_pending_created_at"] = int(record.get("created_at") or 0)
        return signal

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
            self._remove_key(state, key)
            return False, current_reason, None

        record = pending.get(key)

        if isinstance(record, dict):
            anchor = record.get("signal")
            if isinstance(anchor, dict):
                anchored_signal = _merge_anchor(anchor, signal)

                anchor_ok, anchor_reason = base_validator(
                    anchored_signal,
                    current_price,
                )
                if not anchor_ok:
                    self._remove_key(state, key)
                    return False, anchor_reason, None

                result = self.premium_gate.evaluate(
                    anchored_signal,
                    current_price,
                )
                reason = str(result.get("reason") or "")

                if result.get("ok"):
                    # Main market guard güncel koşulda sinyali RADAR'a düşürdüyse,
                    # fiyat teyidi gelmiş olsa bile adayı kaybetme. Market yönü
                    # uygun olduğunda sonraki tur TRADE olarak yeniden değerlendirilir.
                    if str(signal.get("signal_class") or "").upper() != "TRADE":
                        record["last_seen_at"] = self._now()
                        record["latest_score"] = signal.get("score")
                        pending[key] = record
                        self._save_state(state)
                        return (
                            False,
                            "Premium V3: market yön teyidi bekleniyor",
                            result,
                        )

                    signal.clear()
                    signal.update(anchored_signal)
                    self._annotate_confirmation(signal, record, "CONFIRMED")
                    self._remove_key(state, key)
                    return True, "Premium V3 teyitli", result

                if reason in WAIT_REASONS:
                    record["last_seen_at"] = self._now()
                    record["latest_score"] = signal.get("score")
                    pending[key] = record
                    self._save_state(state)
                    return False, "Premium V3: fiyat teyidi bekleniyor", result

                self.premium_gate.reject(
                    anchored_signal,
                    current_price,
                    result,
                )
                self._remove_key(state, key)
                return False, "Profit V3: " + reason, result

            self._remove_key(state, key)

        result = self.premium_gate.evaluate(signal, current_price)
        reason = str(result.get("reason") or "")

        if result.get("ok"):
            # Teyit tamam ama main market guard yönü henüz uygun görmüyorsa,
            # adayı sabitle ve sonraki 5 dakikalık turlarda tekrar dene.
            if str(signal.get("signal_class") or "").upper() != "TRADE":
                if self._evidence_allows(signal):
                    self._store_pending(state, signal, current_price)
                return (
                    False,
                    "Premium V3: market yön teyidi bekleniyor",
                    result,
                )

            self._annotate_confirmation(signal, None, "DIRECT")
            return True, "Premium V3 uygun", result

        if reason in WAIT_REASONS and self._evidence_allows(signal):
            self._store_pending(state, signal, current_price)
            return False, "Premium V3: fiyat teyidi bekleniyor", result

        self.premium_gate.reject(signal, current_price, result)
        return False, "Profit V3: " + reason, result

    def pending_count(self) -> int:
        state = self._state()
        return len(state.get("pending") or {})
