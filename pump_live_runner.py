"""Pump/Dump canlı sinyal + erken büyük hareket görünürlük katmanı.

Mevcut Pump/Dump gerçek sinyal eşiklerini değiştirmez. Buna ek olarak sessiz
trend gözleminde GPS benzeri güçlü 15M/30M hareketleri Telegram'a erken uyarı
olarak çıkarır ve aynı coindeki eski ters-yön sinyalinin yeni fırsatı bloklamasını
önler. Gerçek emir açmaz.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

import opportunity_capture as capture

SCALP_LEDGER_FILE = "scalp_performance_ledger.json"
SHADOW_ALERT_COOLDOWN_MINUTES = 45
MAX_SHADOW_ALERTS_PER_RUN = 2
SHADOW_ALERT_MIN_15M_PERCENT = 0.90
SHADOW_ALERT_MIN_30M_PERCENT = 1.20
SCALP_CONFIRM_LOOKBACK_MINUTES = 120


def safe_load_json(path: str) -> Dict[str, Any]:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def recent_scalp_confirmation(
    radar: Any,
    symbol: str,
    direction: str,
) -> Optional[Dict[str, Any]]:
    ledger = safe_load_json(SCALP_LEDGER_FILE)
    records = ledger.get("records")
    if not isinstance(records, list):
        return None

    cutoff = radar.now_ts() - SCALP_CONFIRM_LOOKBACK_MINUTES * 60
    normalized_symbol = radar.normalize_bot_symbol(symbol)
    normalized_direction = str(direction or "").upper()

    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        sent_at = int(record.get("sent_at") or 0)
        if sent_at and sent_at < cutoff:
            break
        if radar.normalize_bot_symbol(record.get("symbol")) != normalized_symbol:
            continue
        if str(record.get("direction") or "").upper() != normalized_direction:
            continue
        if str(record.get("stage") or "").upper() not in {"PREWATCH", "EARLY", "REAL_SIGNAL"}:
            continue
        return record
    return None


def shadow_event_is_alert_worthy(radar: Any, event: Dict[str, Any]) -> bool:
    move15 = abs(radar.safe_float(event.get("move15_percent")))
    move30 = abs(radar.safe_float(event.get("move30_percent")))
    return bool(event.get("shadow_ready")) or (
        move15 >= SHADOW_ALERT_MIN_15M_PERCENT
        or move30 >= SHADOW_ALERT_MIN_30M_PERCENT
    )


def build_shadow_alert_message(radar: Any, event: Dict[str, Any]) -> str:
    direction = str(event.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    scalp = recent_scalp_confirmation(radar, str(event.get("symbol") or ""), direction)
    if scalp:
        scalp_text = (
            f"✅ SCALP {str(scalp.get('stage') or '').upper()} aynı yönde görüldü"
        )
    else:
        scalp_text = "— Aynı yönde yakın Scalp kaydı yok"

    missing = list(event.get("trend_missing") or [])
    missing_text = ", ".join(missing[:3]) if missing else "eksik teyit yok"
    status = "TREND DEVAM HAZIR" if event.get("shadow_ready") else "ERKEN BÜYÜK HAREKET"

    return (
        "🚨 BÜYÜK HAREKET ERKEN UYARISI\n\n"
        f"{icon} {direction} | {event.get('symbol')}\n"
        f"Durum: {status}\n"
        f"Fiyat: {radar.format_price(event.get('price'))}\n"
        f"15M hareket: %{radar.safe_float(event.get('move15_percent')):+.2f}\n"
        f"30M hareket: %{radar.safe_float(event.get('move30_percent')):+.2f}\n"
        f"5M hacim: {radar.safe_float(event.get('vol5')):.2f}x\n"
        f"5M RSI: {radar.safe_float(event.get('rsi5')):.1f}\n"
        f"Çapraz sistem teyidi: {scalp_text}\n"
        f"Henüz eksik olabilecek teyitler: {missing_text}\n\n"
        "📌 Bu uyarı klasik Pump/Dump işlem filtresinin bütün şartlarını beklemez. "
        "Amaç GPS benzeri hızlanan LONG/SHORT fırsatını hareket bitmeden haber vermektir.\n"
        "🔄 Aynı coinde daha önce ters yön sinyali olması bu uyarıyı ENGELLEMEZ.\n"
        "⚠️ Erken uyarıdır; girişte güncel grafik ve stop yapısı kontrol edilmelidir."
    )


def make_shadow_saver(
    radar: Any,
    original: Callable[..., int],
) -> Callable[..., int]:
    if getattr(original, "_visible_big_move_wrapped", False):
        return original

    def wrapped(state: Dict[str, Any], events: List[Dict[str, Any]]) -> int:
        state.setdefault("shadow_alert_last_sent", {})
        now = radar.now_ts()
        shadow_duplicate_seconds = int(radar.SHADOW_DUPLICATE_MINUTES) * 60
        alert_cooldown_seconds = SHADOW_ALERT_COOLDOWN_MINUTES * 60

        eligible: List[Dict[str, Any]] = []
        for event in events or []:
            if not isinstance(event, dict) or not shadow_event_is_alert_worthy(radar, event):
                continue
            key = f"{event.get('symbol')}_{event.get('direction')}"
            last_shadow = int(state.get("shadow_last_seen", {}).get(key, 0) or 0)
            last_alert = int(state["shadow_alert_last_sent"].get(key, 0) or 0)
            if now - last_shadow < shadow_duplicate_seconds:
                continue
            if now - last_alert < alert_cooldown_seconds:
                continue
            eligible.append(event)

        eligible.sort(
            key=lambda item: max(
                abs(radar.safe_float(item.get("move15_percent"))),
                abs(radar.safe_float(item.get("move30_percent"))),
            ),
            reverse=True,
        )

        sent = 0
        for event in eligible[:MAX_SHADOW_ALERTS_PER_RUN]:
            key = f"{event.get('symbol')}_{event.get('direction')}"
            bucket = now // max(1, alert_cooldown_seconds)
            if radar.send_telegram(
                build_shadow_alert_message(radar, event),
                delivery_key=f"BIG_MOVE_EARLY|{key}|{bucket}",
            ):
                state["shadow_alert_last_sent"][key] = now
                sent += 1

        added = original(state, events)
        if sent and not added:
            radar.save_state(state)
        print("Telegram büyük hareket erken uyarısı:", sent)
        return added

    wrapped._visible_big_move_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def apply_opportunity_capture(radar: Any) -> None:
    radar.has_open_same_symbol = lambda state, symbol: False
    radar.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        radar.evaluate_portfolio_risk
    )
    radar.save_shadow_events = make_shadow_saver(radar, radar.save_shadow_events)


def run(radar: Any | None = None) -> None:
    if radar is None:
        import pump_radar as radar  # type: ignore[no-redef]

    apply_opportunity_capture(radar)
    print("Fırsat yakalama: sessiz büyük hareket Telegram AÇIK | ters-yön fırsatı ENGELLENMEZ")
    radar.main()


if __name__ == "__main__":
    run()
