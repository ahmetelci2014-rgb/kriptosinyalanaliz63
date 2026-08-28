"""Near-miss paper alerts for Simple Core.

Purpose
-------
Give the user a small number of clearly labelled TEST/PAPER candidates to
watch manually without weakening the live strategy.

A paper candidate must already have:
- valid 1H direction,
- a valid 15M swing zone,
- valid structural stop geometry,
- at least 2R room.

It is eligible only when exactly ONE late live gate is missing:
- 15M rejection, or
- 5M trigger.

Paper alerts never enter open_signals/trade_ledger and never place exchange
orders. They are recorded separately for later comparison.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
import time

import simple_core_strategy as core
from telegram_delivery import send_telegram_once

VERSION = "SIMPLE_CORE_PAPER_V1_2026_08_28"
SOURCE = "SIMPLE_CORE_PAPER_V1"
STATE_FILE = "simple_core_paper_candidates.json"
MAX_PAPER_PER_RUN = 2
PAPER_DUPLICATE_SECONDS = 2 * 60 * 60
STATE_KEEP_RECORDS = 300


def _safe(value: Any, default: float = 0.0) -> float:
    return core._safe(value, default)


def build_paper_candidate(
    symbol: str,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: Optional[float],
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Build a near-live paper candidate or return its rejection reason."""
    direction, trend_reason, trend_info = core._one_hour_direction(df1h)
    if direction is None:
        return None, trend_reason

    frame15 = core.indicators.add_indicators(df15m)
    if frame15 is None or len(frame15) < 40:
        return None, "15M_DATA"

    last15 = frame15.iloc[-2]
    entry = _safe(current_price) if _safe(current_price) > 0 else _safe(last15["close"])
    atr = _safe(last15["atr"])
    if entry <= 0 or atr <= 0:
        return None, "15M_DATA"

    atr_percent = atr / entry * 100.0
    zone, zone_distance, zone_name = core._find_zone(
        direction,
        frame15,
        entry,
        atr_percent,
    )
    if zone is None:
        return None, zone_name

    rejection_ok, rejection_reason = core._fifteen_minute_rejection(
        direction,
        frame15,
        zone,
        atr_percent,
    )
    trigger_ok, trigger_reason, trigger_info = core._five_minute_trigger(
        direction,
        df5m,
    )
    if trigger_reason == "5M_DATA":
        return None, "5M_DATA"

    # Live-ready setups belong to the real Simple Core path, not paper.
    if rejection_ok and trigger_ok:
        return None, "LIVE_READY"

    missing: List[str] = []
    if not rejection_ok:
        missing.append("15M_NO_REJECTION")
    if not trigger_ok:
        missing.append("5M_NO_CONFIRM")

    # Do not spam weak setups. Exactly one late confirmation may be missing.
    if len(missing) != 1:
        return None, "PAPER_MULTI_MISS"

    targets, risk_reason = core._targets_and_room(
        direction,
        frame15,
        entry,
        zone,
        atr,
    )
    if targets is None:
        return None, risk_reason

    missing_gate = missing[0]
    missing_text = (
        "15M bölge dönüş mumu eksik"
        if missing_gate == "15M_NO_REJECTION"
        else "5M giriş kırılım teyidi eksik"
    )

    score = 70
    adx_1h = _safe(trend_info.get("adx_1h"))
    volume_5m = _safe(trigger_info.get("volume_5m"))
    if adx_1h >= 25:
        score += 5
    if zone_distance <= 0.25:
        score += 5
    if volume_5m >= 1.30:
        score += 5
    if _safe(targets.get("room_r")) >= 3.0:
        score += 5
    if rejection_ok:
        score += 5
    if trigger_ok:
        score += 5
    score = min(95, score)

    return {
        "symbol": str(symbol).upper(),
        "direction": direction,
        "source": SOURCE,
        "signal_class": "PAPER",
        "entry": round(entry, 10),
        "ideal_entry": round(zone, 10),
        "zone_distance_percent": round(zone_distance, 3),
        "zone_name": zone_name,
        "tp1": targets["tp1"],
        "tp2": targets["tp2"],
        "tp3": targets["tp3"],
        "sl": targets["sl"],
        "risk_percent": targets["risk_percent"],
        "room_r": targets["room_r"],
        "rr_tp1": targets["rr_tp1"],
        "rr_tp2": targets["rr_tp2"],
        "rr_tp3": targets["rr_tp3"],
        "score": score,
        "trend_reason": trend_reason,
        "rejection_ok": bool(rejection_ok),
        "rejection_reason": rejection_reason,
        "trigger_ok": bool(trigger_ok),
        "trigger_reason": trigger_reason,
        "paper_missing_gate": missing_gate,
        "paper_missing_text": missing_text,
        "adx_1h": trend_info.get("adx_1h"),
        "rsi_1h": trend_info.get("rsi_1h"),
        "volume_5m": trigger_info.get("volume_5m"),
        "rsi_5m": trigger_info.get("rsi_5m"),
        "close_power_5m": trigger_info.get("close_power_5m"),
        "paper_version": VERSION,
    }, "PAPER_READY"


def _load_state(bot: Any) -> Dict[str, Any]:
    data = bot.load_json_file(STATE_FILE, {"version": VERSION, "records": []})
    if not isinstance(data, dict):
        data = {}
    records = data.get("records")
    if not isinstance(records, list):
        records = []
    data["version"] = VERSION
    data["records"] = records
    return data


def _recent_duplicate(records: Iterable[Dict[str, Any]], candidate: Dict[str, Any], now: int) -> bool:
    for row in reversed(list(records)):
        if not isinstance(row, dict):
            continue
        at = int(row.get("sent_at") or 0)
        if at <= 0:
            continue
        if now - at > PAPER_DUPLICATE_SECONDS:
            break
        if (
            str(row.get("symbol") or "").upper() == str(candidate.get("symbol") or "").upper()
            and str(row.get("direction") or "").upper() == str(candidate.get("direction") or "").upper()
            and str(row.get("paper_missing_gate") or "") == str(candidate.get("paper_missing_gate") or "")
        ):
            return True
    return False


def _price(bot: Any, value: Any) -> str:
    try:
        return bot.format_price(value)
    except Exception:
        number = _safe(value)
        return f"{number:.8f}" if number else "-"


def format_paper_message(bot: Any, candidate: Dict[str, Any]) -> str:
    direction = str(candidate.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    return (
        "🧪 TEST / PAPER — GERÇEK İŞLEM DEĞİL\n"
        f"{icon} {direction} | {candidate.get('symbol')}\n"
        f"📍 Test giriş: {_price(bot, candidate.get('entry'))}\n"
        f"🧱 Referans bölge: {_price(bot, candidate.get('ideal_entry'))} "
        f"(%{_safe(candidate.get('zone_distance_percent')):.3f})\n"
        f"🛑 SL: {_price(bot, candidate.get('sl'))} | risk %{_safe(candidate.get('risk_percent')):.2f}\n"
        f"🎯 TP1: {_price(bot, candidate.get('tp1'))}\n"
        f"🎯 TP2: {_price(bot, candidate.get('tp2'))}\n"
        f"🎯 TP3: {_price(bot, candidate.get('tp3'))}\n"
        f"📐 Karşı seviyeye alan: {_safe(candidate.get('room_r')):.2f}R\n"
        f"🧭 1H: {candidate.get('trend_reason')}\n"
        f"⚠️ Canlıya geçmeme nedeni: {candidate.get('paper_missing_text')}\n"
        f"🧠 Test yakınlık skoru: {int(candidate.get('score') or 0)}/95\n"
        "🔬 Amaç: canlı kuralları gevşetmeden yakın adayın sonucunu gözlemlemek.\n"
        "❗ Bu mesaj canlı işlem sinyali veya otomatik emir değildir."
    )


def send_paper_candidates(
    bot: Any,
    candidates: Iterable[Dict[str, Any]],
    *,
    live_run: bool,
    live_candidate_exists: bool,
) -> int:
    """Send at most two paper alerts when there is no real live candidate."""
    rows = [item for item in candidates if isinstance(item, dict)]
    if not live_run or live_candidate_exists or not rows:
        return 0

    rows.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            _safe(item.get("room_r")),
            -_safe(item.get("zone_distance_percent"), 999.0),
        ),
        reverse=True,
    )

    state = _load_state(bot)
    records = state["records"]
    now = int(time.time())
    sent = 0

    for candidate in rows:
        if sent >= MAX_PAPER_PER_RUN:
            break
        if _recent_duplicate(records, candidate, now):
            continue

        message = format_paper_message(bot, candidate)
        bucket = now // PAPER_DUPLICATE_SECONDS
        delivery_key = (
            f"PAPER|{candidate.get('symbol')}|{candidate.get('direction')}|"
            f"{candidate.get('paper_missing_gate')}|{bucket}"
        )
        ok = send_telegram_once(
            message=message,
            telegram_token=bot.TOKEN,
            chat_id=bot.CHAT_ID,
            bot_key="SIMPLE_CORE_PAPER",
            delivery_key=delivery_key,
        )
        if not ok:
            continue

        record = dict(candidate)
        record["sent_at"] = now
        record["status"] = "PAPER_SENT"
        records.append(record)
        sent += 1

    state["records"] = records[-STATE_KEEP_RECORDS:]
    state["last_update"] = now
    bot.save_json_file(STATE_FILE, state)
    return sent
