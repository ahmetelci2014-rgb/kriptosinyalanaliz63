"""Telegram early-warning layer for Market Structure AI.

This module does NOT create trades or orders. It only turns already-detected
Market Structure WATCH/READY events into clearly labelled informational alerts.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from telegram_delivery import send_telegram_once

VERSION = "MARKET_STRUCTURE_EARLY_ALERTS_V1_2026_08_26"
BOT_KEY = "STRUCTURE_ALERT"

WATCH_MIN_SCORE = int(os.getenv("MARKET_STRUCTURE_WATCH_ALERT_SCORE", "64"))
WATCH_MIN_DIRECTION_GAP = int(os.getenv("MARKET_STRUCTURE_WATCH_DIRECTION_GAP", "14"))
WATCH_MAX_ORIGIN_ATR = float(os.getenv("MARKET_STRUCTURE_WATCH_MAX_ORIGIN_ATR", "1.80"))
READY_MIN_SCORE = int(os.getenv("MARKET_STRUCTURE_READY_ALERT_SCORE", "72"))
READY_MIN_DIRECTION_GAP = int(os.getenv("MARKET_STRUCTURE_READY_DIRECTION_GAP", "10"))
READY_MAX_ORIGIN_ATR = float(os.getenv("MARKET_STRUCTURE_READY_MAX_ORIGIN_ATR", "2.80"))


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _truthy_count(conditions: Dict[str, Any], names: tuple[str, ...]) -> int:
    return sum(1 for name in names if bool(conditions.get(name)))


def should_alert(event: Dict[str, Any]) -> Tuple[bool, str]:
    result = event.get("result") or {}
    record = event.get("record") or {}
    conditions = result.get("conditions") or record.get("conditions") or {}

    stage = str(result.get("stage") or record.get("stage") or "").upper()
    score = int(result.get("score") or record.get("score") or 0)
    opposite = int(result.get("opposite_score") or record.get("opposite_score") or 0)
    direction_gap = score - opposite
    distance_atr = _sf(
        result.get("origin_distance_atr", record.get("origin_distance_atr")),
        999.0,
    )

    if stage == "READY":
        if score < READY_MIN_SCORE:
            return False, "READY_SCORE_LOW"
        if direction_gap < READY_MIN_DIRECTION_GAP:
            return False, "READY_DIRECTION_AMBIGUOUS"
        if distance_atr > READY_MAX_ORIGIN_ATR:
            return False, "READY_TOO_FAR_FROM_ORIGIN"
        return True, "READY_STRUCTURE_CONFIRMED"

    if stage != "WATCH":
        return False, "NOT_ALERT_STAGE"
    if score < WATCH_MIN_SCORE:
        return False, "WATCH_SCORE_LOW"
    if direction_gap < WATCH_MIN_DIRECTION_GAP:
        return False, "WATCH_DIRECTION_AMBIGUOUS"
    if distance_atr > WATCH_MAX_ORIGIN_ATR:
        return False, "WATCH_TOO_FAR_FROM_ORIGIN"
    if not bool(conditions.get("fifteen_not_opposing", True)):
        return False, "WATCH_15M_OPPOSING"

    origin_evidence = _truthy_count(
        conditions,
        ("structure_shift", "zone_touch", "sweep_reclaim", "double_extreme"),
    )
    turn_evidence = _truthy_count(
        conditions,
        ("trendline_break", "choch", "bos", "ema_turn"),
    )
    if origin_evidence < 2:
        return False, "WATCH_ORIGIN_EVIDENCE_WEAK"
    if turn_evidence < 1:
        return False, "WATCH_TURN_NOT_STARTED"

    return True, "WATCH_EARLY_STRUCTURE"


def _feature_labels(conditions: Dict[str, Any]) -> list[str]:
    labels = []
    mapping = (
        ("structure_shift", "HL/LH dönüşü"),
        ("zone_touch", "S/R bölgesi"),
        ("sweep_reclaim", "likidite sweep/reclaim"),
        ("double_extreme", "çift dip/tepe"),
        ("trendline_break", "trend çizgisi kırılımı"),
        ("choch", "CHOCH"),
        ("bos", "BOS"),
        ("volume_wake", "hacim uyanışı"),
        ("impulse", "impuls"),
        ("ema_turn", "EMA dönüşü"),
    )
    for key, label in mapping:
        if conditions.get(key):
            labels.append(label)
    return labels[:6]


def build_message(event: Dict[str, Any]) -> str:
    result = event.get("result") or {}
    record = event.get("record") or {}
    conditions = result.get("conditions") or record.get("conditions") or {}

    symbol = str(result.get("symbol") or record.get("symbol") or "?").upper()
    direction = str(result.get("direction") or record.get("direction") or "?").upper()
    stage = str(result.get("stage") or record.get("stage") or "WATCH").upper()
    score = int(result.get("score") or record.get("score") or 0)
    origin = _sf(record.get("origin", result.get("origin")))
    entry = _sf(record.get("entry", result.get("entry")))
    distance_pct = _sf(record.get("origin_distance_percent", result.get("origin_distance_percent")))
    distance_atr = _sf(record.get("origin_distance_atr", result.get("origin_distance_atr")))
    stop = _sf(record.get("stop", result.get("stop")))
    target2 = _sf(record.get("target_2r", result.get("target_2r")))

    if stage == "READY":
        title = "🟠 YAPI TEYİDİ — İŞLEM DEĞİL"
        note = "Yapı kırılımı teyit edildi; Premium işlem şartları ayrıca beklenir."
    else:
        title = "🟡 ERKEN HAREKET UYARISI — İŞLEM DEĞİL"
        note = "Dip/tepe çevresinde yön değişimi hazırlığı görülüyor; henüz işlem sinyali değil."

    side = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    features = ", ".join(_feature_labels(conditions)) or "erken yapı değişimi"

    return "\n".join(
        [
            title,
            f"{side} | {symbol}",
            f"📍 Origin: {origin:.8g}",
            f"👀 İzleme fiyatı: {entry:.8g}",
            f"📏 Origin uzaklığı: %{distance_pct:.2f} | {distance_atr:.2f} ATR",
            f"🧠 Yapı skoru: {score}/100",
            f"🔎 Kanıt: {features}",
            f"🧱 Referans stop bölgesi: {stop:.8g}",
            f"🎯 2R araştırma seviyesi: {target2:.8g}",
            "",
            note,
            "⚠️ Bu mesaj otomatik emir veya kesin al/sat tavsiyesi değildir.",
        ]
    )


def delivery_key(event: Dict[str, Any]) -> str:
    result = event.get("result") or {}
    record = event.get("record") or {}
    symbol = str(result.get("symbol") or record.get("symbol") or "UNKNOWN").upper()
    direction = str(result.get("direction") or record.get("direction") or "UNKNOWN").upper()
    stage = str(result.get("stage") or record.get("stage") or "UNKNOWN").upper()
    started_at = int(record.get("started_at") or 0)
    return f"MSA|{symbol}|{direction}|{stage}|{started_at}"


def send_event(event: Dict[str, Any], telegram_token: Any, chat_id: Any) -> Tuple[bool, str]:
    allowed, reason = should_alert(event)
    if not allowed:
        return False, reason
    message = build_message(event)
    ok = send_telegram_once(
        message=message,
        telegram_token=telegram_token,
        chat_id=chat_id,
        bot_key=BOT_KEY,
        delivery_key=delivery_key(event),
    )
    return bool(ok), reason if ok else "TELEGRAM_SEND_FAILED"
