from __future__ import annotations

import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

VERSION = "PREMIUM_PAPER_AUTO_V1_2026_08_18"
MODE = "PAPER_ONLY_NO_PRIVATE_API_NO_REAL_ORDERS"
OPEN_SIGNALS_FILE = "open_signals.json"
STATE_FILE = "paper_demo_state.json"
BASE_URL = "https://www.okx.com"

MIN_SCORE = int(os.getenv("PAPER_MIN_SCORE", "91"))
MAX_SIGNAL_AGE_MINUTES = int(os.getenv("PAPER_MAX_SIGNAL_AGE_MINUTES", "30"))
MAX_ENTRY_DRIFT_PERCENT = float(os.getenv("PAPER_MAX_ENTRY_DRIFT_PERCENT", "0.25"))
MARGIN_USDT = float(os.getenv("PAPER_MARGIN_USDT", "5"))
LEVERAGE = int(os.getenv("PAPER_LEVERAGE", "2"))
MAX_HISTORY = 200


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data
    except Exception:
        return default


def atomic_save(path: str, data: dict[str, Any]) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        json.loads(tmp_path.read_text(encoding="utf-8"))
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def empty_state() -> dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "paper_margin_usdt": MARGIN_USDT,
        "paper_leverage": LEVERAGE,
        "open_position": None,
        "history": [],
        "stats": {},
        "last_update": 0,
    }


def normalize_state(raw: Any) -> dict[str, Any]:
    state = raw if isinstance(raw, dict) else empty_state()
    state["version"] = VERSION
    state["mode"] = MODE
    state["paper_margin_usdt"] = MARGIN_USDT
    state["paper_leverage"] = LEVERAGE
    if not isinstance(state.get("history"), list):
        state["history"] = []
    if state.get("open_position") is not None and not isinstance(
        state.get("open_position"), dict
    ):
        state["open_position"] = None
    return state


def okx_inst_id(symbol: str) -> str:
    normalized = str(symbol or "").upper().replace("/", "").replace("-", "")
    if not normalized.endswith("USDT"):
        raise ValueError(f"Yalnız USDT pariteleri destekleniyor: {symbol}")
    base = normalized[:-4]
    if not base:
        raise ValueError(f"Geçersiz sembol: {symbol}")
    return f"{base}-USDT-SWAP"


def pct_distance(a: float, b: float) -> float:
    if b <= 0:
        return 999.0
    return abs(a - b) / b * 100.0


def public_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    response = requests.get(BASE_URL + path, params=params, timeout=12)
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("code", "0")) != "0":
        raise RuntimeError(
            f"OKX public hata: {payload.get('code')} {payload.get('msg')}"
        )
    return payload


def fetch_last_price(inst_id: str) -> float:
    payload = public_get("/api/v5/market/ticker", {"instId": inst_id})
    rows = payload.get("data") or []
    if not rows:
        raise RuntimeError(f"OKX fiyat verisi yok: {inst_id}")
    last = safe_float(rows[0].get("last"))
    if not last or last <= 0:
        raise RuntimeError(f"Geçersiz OKX fiyatı: {inst_id}")
    return last


def fetch_recent_1m_candles(inst_id: str, limit: int = 20) -> list[dict[str, Any]]:
    payload = public_get(
        "/api/v5/market/candles",
        {"instId": inst_id, "bar": "1m", "limit": str(max(2, min(limit, 100)))},
    )
    rows = payload.get("data") or []
    candles: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        ts = safe_int(row[0])
        o = safe_float(row[1])
        h = safe_float(row[2])
        l = safe_float(row[3])
        c = safe_float(row[4])
        if ts and all(x is not None for x in (o, h, l, c)):
            candles.append(
                {
                    "ts": ts // 1000,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                }
            )
    candles.sort(key=lambda item: item["ts"])
    return candles


def signal_is_structurally_eligible(
    signal: dict[str, Any],
    now_ts: int,
    min_score: int = MIN_SCORE,
    max_age_minutes: int = MAX_SIGNAL_AGE_MINUTES,
) -> bool:
    if not isinstance(signal, dict):
        return False
    if signal.get("closed") or signal.get("tp1_hit"):
        return False
    if safe_float(signal.get("score"), 0.0) < min_score:
        return False
    sent_distance = safe_float(
        signal.get("entry_distance_at_send_percent"), 999.0
    )
    if sent_distance is None or sent_distance > MAX_ENTRY_DRIFT_PERCENT:
        return False
    opened_at = safe_int(signal.get("opened_at"))
    if opened_at <= 0 or now_ts - opened_at > max_age_minutes * 60:
        return False
    direction = str(signal.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False
    prices = [
        safe_float(signal.get(name))
        for name in ("entry", "sl", "tp1", "tp2", "tp3")
    ]
    return all(value is not None and value > 0 for value in prices)


def choose_candidate(
    open_signals: dict[str, Any],
    now_ts: int,
    min_score: int = MIN_SCORE,
    max_age_minutes: int = MAX_SIGNAL_AGE_MINUTES,
) -> dict[str, Any] | None:
    candidates = [
        signal
        for signal in (open_signals or {}).values()
        if signal_is_structurally_eligible(
            signal, now_ts, min_score=min_score, max_age_minutes=max_age_minutes
        )
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            safe_float(item.get("score"), 0.0),
            safe_int(item.get("opened_at")),
        ),
        reverse=True,
    )
    return candidates[0]


def candidate_can_fill(signal: dict[str, Any], last_price: float) -> tuple[bool, str]:
    entry = safe_float(signal.get("entry"), 0.0) or 0.0
    sl = safe_float(signal.get("sl"), 0.0) or 0.0
    tp1 = safe_float(signal.get("tp1"), 0.0) or 0.0
    direction = str(signal.get("direction") or "").upper()

    drift = pct_distance(last_price, entry)
    if drift > MAX_ENTRY_DRIFT_PERCENT:
        return False, f"güncel fiyat girişten %{drift:.3f} uzak"

    if direction == "LONG":
        if not (sl < last_price < tp1):
            return False, "LONG için güncel fiyat artık SL-TP1 güvenli aralığında değil"
    elif direction == "SHORT":
        if not (tp1 < last_price < sl):
            return False, "SHORT için güncel fiyat artık TP1-SL güvenli aralığında değil"
    else:
        return False, "yön geçersiz"

    return True, "uygun"


def open_paper_position(signal: dict[str, Any], fill_price: float, now_ts: int) -> dict[str, Any]:
    notional = MARGIN_USDT * LEVERAGE
    return {
        "paper_id": f"PAPER_{signal.get('trade_id') or signal.get('symbol')}_{now_ts}",
        "source_trade_id": signal.get("trade_id"),
        "symbol": signal.get("symbol"),
        "inst_id": okx_inst_id(str(signal.get("symbol") or "")),
        "direction": str(signal.get("direction") or "").upper(),
        "source": signal.get("source"),
        "score": safe_float(signal.get("score")),
        "quality": signal.get("quality"),
        "signal_entry": safe_float(signal.get("entry")),
        "fill_price": float(fill_price),
        "sl_original": safe_float(signal.get("sl")),
        "active_stop": safe_float(signal.get("sl")),
        "tp1": safe_float(signal.get("tp1")),
        "tp2": safe_float(signal.get("tp2")),
        "tp3": safe_float(signal.get("tp3")),
        "margin_usdt": MARGIN_USDT,
        "leverage": LEVERAGE,
        "notional_usdt": notional,
        "remaining_fraction": 1.0,
        "realized_pnl_usdt": 0.0,
        "tp1_hit": False,
        "tp1_hit_at": None,
        "tp2_hit": False,
        "tp2_hit_at": None,
        "opened_at": now_ts,
        "last_checked_at": now_ts,
        "last_price": float(fill_price),
        "status": "OPEN",
        "final_result": None,
        "closed_at": None,
        "exit_price": None,
        "entry_drift_percent": round(
            pct_distance(float(fill_price), float(signal.get("entry"))), 4
        ),
    }


def pnl_for_fraction(
    position: dict[str, Any], exit_price: float, fraction: float
) -> float:
    fill = safe_float(position.get("fill_price"), 0.0) or 0.0
    notional = safe_float(position.get("notional_usdt"), 0.0) or 0.0
    if fill <= 0 or notional <= 0 or fraction <= 0:
        return 0.0
    direction = str(position.get("direction") or "").upper()
    move = (
        (exit_price - fill) / fill
        if direction == "LONG"
        else (fill - exit_price) / fill
    )
    return notional * fraction * move


def close_position(
    position: dict[str, Any],
    result: str,
    exit_price: float,
    now_ts: int,
) -> None:
    remaining = safe_float(position.get("remaining_fraction"), 0.0) or 0.0
    if remaining > 0:
        position["realized_pnl_usdt"] = round(
            (safe_float(position.get("realized_pnl_usdt"), 0.0) or 0.0)
            + pnl_for_fraction(position, exit_price, remaining),
            6,
        )
    position["remaining_fraction"] = 0.0
    position["status"] = "CLOSED"
    position["final_result"] = result
    position["exit_price"] = float(exit_price)
    position["closed_at"] = now_ts
    position["last_checked_at"] = now_ts
    position["last_price"] = float(exit_price)


def _hit_long(position: dict[str, Any], candle: dict[str, Any]) -> None:
    ts = safe_int(candle.get("ts"))
    low = safe_float(candle.get("low"), 0.0) or 0.0
    high = safe_float(candle.get("high"), 0.0) or 0.0
    active_stop = safe_float(position.get("active_stop"), 0.0) or 0.0

    if low <= active_stop:
        result = "BE_AFTER_TP1" if position.get("tp1_hit") else "SL"
        close_position(position, result, active_stop, ts)
        return

    if not position.get("tp1_hit") and high >= float(position["tp1"]):
        fraction = 0.5
        position["realized_pnl_usdt"] = round(
            pnl_for_fraction(position, float(position["tp1"]), fraction), 6
        )
        position["remaining_fraction"] = 0.5
        position["tp1_hit"] = True
        position["tp1_hit_at"] = ts
        position["active_stop"] = float(position["fill_price"])

    if position.get("status") != "OPEN":
        return

    if not position.get("tp2_hit") and high >= float(position["tp2"]):
        position["tp2_hit"] = True
        position["tp2_hit_at"] = ts

    if high >= float(position["tp3"]):
        close_position(position, "TP3", float(position["tp3"]), ts)


def _hit_short(position: dict[str, Any], candle: dict[str, Any]) -> None:
    ts = safe_int(candle.get("ts"))
    low = safe_float(candle.get("low"), 0.0) or 0.0
    high = safe_float(candle.get("high"), 0.0) or 0.0
    active_stop = safe_float(position.get("active_stop"), 0.0) or 0.0

    if high >= active_stop:
        result = "BE_AFTER_TP1" if position.get("tp1_hit") else "SL"
        close_position(position, result, active_stop, ts)
        return

    if not position.get("tp1_hit") and low <= float(position["tp1"]):
        fraction = 0.5
        position["realized_pnl_usdt"] = round(
            pnl_for_fraction(position, float(position["tp1"]), fraction), 6
        )
        position["remaining_fraction"] = 0.5
        position["tp1_hit"] = True
        position["tp1_hit_at"] = ts
        position["active_stop"] = float(position["fill_price"])

    if position.get("status") != "OPEN":
        return

    if not position.get("tp2_hit") and low <= float(position["tp2"]):
        position["tp2_hit"] = True
        position["tp2_hit_at"] = ts

    if low <= float(position["tp3"]):
        close_position(position, "TP3", float(position["tp3"]), ts)


def process_candles(position: dict[str, Any], candles: list[dict[str, Any]]) -> None:
    if not position or position.get("status") != "OPEN":
        return
    last_checked = safe_int(position.get("last_checked_at"))
    for candle in candles:
        ts = safe_int(candle.get("ts"))
        if ts <= last_checked or position.get("status") != "OPEN":
            continue
        if str(position.get("direction") or "").upper() == "LONG":
            _hit_long(position, candle)
        else:
            _hit_short(position, candle)
        if position.get("status") == "OPEN":
            position["last_checked_at"] = ts
            position["last_price"] = safe_float(
                candle.get("close"), position.get("last_price")
            )


def build_stats(history: list[dict[str, Any]], open_position: dict[str, Any] | None) -> dict[str, Any]:
    closed = [item for item in history if isinstance(item, dict)]
    outcomes: dict[str, int] = {}
    total_pnl = 0.0
    for item in closed:
        result = str(item.get("final_result") or "UNKNOWN")
        outcomes[result] = outcomes.get(result, 0) + 1
        total_pnl += safe_float(item.get("realized_pnl_usdt"), 0.0) or 0.0
    return {
        "closed": len(closed),
        "open": 1 if open_position else 0,
        "outcomes": dict(sorted(outcomes.items())),
        "net_pnl_usdt": round(total_pnl, 6),
    }


def update_paper_state(
    state: dict[str, Any],
    open_signals: dict[str, Any],
    now_ts: int | None = None,
    price_fetcher=fetch_last_price,
    candle_fetcher=fetch_recent_1m_candles,
) -> dict[str, Any]:
    now_ts = int(now_ts or time.time())
    state = normalize_state(state)
    position = state.get("open_position")

    if isinstance(position, dict):
        inst_id = str(position.get("inst_id") or okx_inst_id(position.get("symbol")))
        candles = candle_fetcher(inst_id)
        process_candles(position, candles)
        if position.get("status") == "OPEN":
            last = price_fetcher(inst_id)
            position["last_price"] = last
            position["last_checked_at"] = max(
                safe_int(position.get("last_checked_at")), now_ts
            )
        else:
            state["history"].append(position.copy())
            state["history"] = state["history"][-MAX_HISTORY:]
            state["open_position"] = None
            position = None

    if position is None:
        candidate = choose_candidate(open_signals, now_ts)
        if candidate is not None:
            inst_id = okx_inst_id(str(candidate.get("symbol") or ""))
            last = price_fetcher(inst_id)
            allowed, reason = candidate_can_fill(candidate, last)
            if allowed:
                state["open_position"] = open_paper_position(
                    candidate, last, now_ts
                )
                print(
                    "PAPER_OPEN",
                    candidate.get("symbol"),
                    candidate.get("direction"),
                    "score=", candidate.get("score"),
                    "fill=", last,
                    "sl=", candidate.get("sl"),
                    "tp1=", candidate.get("tp1"),
                    "tp3=", candidate.get("tp3"),
                )
            else:
                print(
                    "PAPER_SKIP",
                    candidate.get("symbol"),
                    reason,
                )
        else:
            print("PAPER_IDLE uygun yeni Premium sinyali yok")

    state["stats"] = build_stats(
        state["history"], state.get("open_position")
    )
    state["last_update"] = now_ts
    state["guardrails"] = {
        "private_api_used": False,
        "real_orders": False,
        "max_open_positions": 1,
        "min_score": MIN_SCORE,
        "max_signal_age_minutes": MAX_SIGNAL_AGE_MINUTES,
        "max_entry_drift_percent": MAX_ENTRY_DRIFT_PERCENT,
        "tp1_management": "close_50_percent_then_stop_to_fill",
        "same_1m_candle_ambiguity": "conservative_stop_first",
    }
    return state


def main() -> None:
    state = load_json(STATE_FILE, empty_state())
    signals = load_json(OPEN_SIGNALS_FILE, {})
    updated = update_paper_state(state, signals)
    atomic_save(STATE_FILE, updated)
    stats = updated["stats"]
    open_position = updated.get("open_position")
    print(
        "PAPER_DEMO",
        "open=", stats["open"],
        "closed=", stats["closed"],
        "net_pnl_usdt=", stats["net_pnl_usdt"],
        "position=", (
            f"{open_position.get('symbol')} {open_position.get('direction')}"
            if open_position
            else "-"
        ),
    )


if __name__ == "__main__":
    main()
