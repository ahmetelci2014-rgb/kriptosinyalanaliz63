"""Pump/Dump canlı sinyal + seçici erken büyük hareket görünürlük katmanı.

Gerçek Pump/Dump kalite şartlarını değiştirmez. Sessiz trend adaylarının tamamı
arka planda tutulur; Telegram'a yalnız Pump'ın kendi devam teyitleri tamamlanan,
Scalp aynı coin/yönü yakın zamanda işaretlemiş veya UNI 2026-08-19 örneğindeki
gibi güçlü iç trend devamı oluşmuş hareket çıkar. Böylece GPS/UNI tipi fırsatlar
görünür kalırken tek başına sert fiyat sıçramaları Telegram'ı doldurmaz.
Eski ters-yön sinyal yeni fırsatı bloklamaz. Gerçek emir açmaz.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

import opportunity_capture as capture

SCALP_LEDGER_FILE = "scalp_performance_ledger.json"
SHADOW_ALERT_COOLDOWN_MINUTES = 45
MAX_SHADOW_ALERTS_PER_RUN = 1
SHADOW_ALERT_MIN_15M_PERCENT = 0.90
SHADOW_ALERT_MIN_30M_PERCENT = 1.20
SCALP_CONFIRM_LOOKBACK_MINUTES = 120

# Güçlü iç trend devamı: Scalp teyidi olmasa bile görünür olur.
# Ama tek başına 15M/30M hareketi yetmez; yönlü 5M yapı + devam teyidi + hacim
# + EMA20 eğimi birlikte aranır. Bu yol gerçek işlem değil, erken fırsat katmanıdır.
STRONG_TREND_MIN_15M_PERCENT = 0.80
STRONG_TREND_MIN_30M_PERCENT = 1.00
STRONG_TREND_MIN_EMA20_SLOPE_PERCENT = 0.15
STRONG_TREND_MAX_EMA20_DISTANCE_PERCENT = 1.10
STRONG_TREND_MIN_5M_VOLUME_RATIO = 1.00
STRONG_TREND_LONG_RSI_MIN = 52
STRONG_TREND_LONG_RSI_MAX = 76
STRONG_TREND_SHORT_RSI_MIN = 24
STRONG_TREND_SHORT_RSI_MAX = 48


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
        if str(record.get("stage") or "").upper() not in {
            "PREWATCH",
            "EARLY",
            "REAL_SIGNAL",
        }:
            continue
        return record
    return None


def movement_is_large_enough(radar: Any, event: Dict[str, Any]) -> bool:
    move15 = abs(radar.safe_float(event.get("move15_percent")))
    move30 = abs(radar.safe_float(event.get("move30_percent")))
    return (
        move15 >= SHADOW_ALERT_MIN_15M_PERCENT
        or move30 >= SHADOW_ALERT_MIN_30M_PERCENT
    )


def strong_internal_trend_confirmation(
    radar: Any,
    event: Dict[str, Any],
) -> bool:
    """Klasik 1M pump filtresi yokken bile güçlü 5M trend devamını yakala."""
    direction = str(event.get("direction") or "").upper()
    move15 = radar.safe_float(event.get("move15_percent"))
    move30 = radar.safe_float(event.get("move30_percent"))
    slope = radar.safe_float(event.get("ema20_slope_percent"))
    distance = abs(radar.safe_float(event.get("ema20_distance_percent")))
    green_count = int(event.get("green_5m_count") or 0)
    red_count = int(event.get("red_5m_count") or 0)
    resume = bool(event.get("resume_confirmed"))
    rsi5 = radar.safe_float(event.get("rsi5"))
    vol5 = radar.safe_float(event.get("vol5"))
    price = radar.safe_float(event.get("price"))
    ema20 = radar.safe_float(event.get("ema20"))

    if (
        distance > STRONG_TREND_MAX_EMA20_DISTANCE_PERCENT
        or vol5 < STRONG_TREND_MIN_5M_VOLUME_RATIO
        or not resume
    ):
        return False

    if direction == "LONG":
        return (
            move15 >= STRONG_TREND_MIN_15M_PERCENT
            and move30 >= STRONG_TREND_MIN_30M_PERCENT
            and slope >= STRONG_TREND_MIN_EMA20_SLOPE_PERCENT
            and green_count >= 4
            and price > ema20
            and STRONG_TREND_LONG_RSI_MIN
            <= rsi5
            <= STRONG_TREND_LONG_RSI_MAX
        )

    if direction == "SHORT":
        return (
            move15 <= -STRONG_TREND_MIN_15M_PERCENT
            and move30 <= -STRONG_TREND_MIN_30M_PERCENT
            and slope <= -STRONG_TREND_MIN_EMA20_SLOPE_PERCENT
            and red_count >= 4
            and price < ema20
            and STRONG_TREND_SHORT_RSI_MIN
            <= rsi5
            <= STRONG_TREND_SHORT_RSI_MAX
        )

    return False


def shadow_event_is_alert_worthy(radar: Any, event: Dict[str, Any]) -> bool:
    """Tek fiyat sıçraması yetmez; kalite, çapraz teyit veya güçlü iç trend gerekir."""
    if bool(event.get("shadow_ready")):
        return True

    if strong_internal_trend_confirmation(radar, event):
        return True

    if not movement_is_large_enough(radar, event):
        return False

    scalp = recent_scalp_confirmation(
        radar,
        str(event.get("symbol") or ""),
        str(event.get("direction") or ""),
    )
    return scalp is not None


def build_shadow_alert_message(radar: Any, event: Dict[str, Any]) -> str:
    direction = str(event.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    scalp = recent_scalp_confirmation(
        radar,
        str(event.get("symbol") or ""),
        direction,
    )
    internal = strong_internal_trend_confirmation(radar, event)

    if scalp:
        confirm_text = (
            f"✅ SCALP {str(scalp.get('stage') or '').upper()} aynı yönde görüldü"
        )
    elif internal:
        confirm_text = "✅ Pump güçlü iç trend devamı teyidi"
    else:
        confirm_text = "✅ Pump trend-devam teyitleri tamamlandı"

    missing = list(event.get("trend_missing") or [])
    missing_text = ", ".join(missing[:3]) if missing else "eksik teyit yok"

    if event.get("shadow_ready"):
        status = "GÜÇLÜ ERKEN TEYİT"
    elif internal:
        status = "GÜÇLÜ TREND DEVAMI — GİRİŞ HAZIRLIĞI"
    else:
        status = "ÇAPRAZ SİSTEM ERKEN TEYİDİ"

    rsi5 = radar.safe_float(event.get("rsi5"))
    late_warning = ""
    if (direction == "LONG" and rsi5 >= 78) or (direction == "SHORT" and rsi5 <= 22):
        late_warning = (
            "\n⛔ RSI aşırı bölgede: hareketin peşinden koşma; "
            "gerçek giriş onayını bekle."
        )

    return (
        "⚠️ YAKIN TAKİP — HENÜZ İŞLEM GİRİŞİ DEĞİL\n"
        "Gerçek işlem girişi ayrıca Giriş + TP + SL ile gelecek.\n\n"
        "🚨 BÜYÜK HAREKET ERKEN UYARISI\n\n"
        f"{icon} {direction} | {event.get('symbol')}\n"
        f"Durum: {status}\n"
        f"Takip fiyatı: {radar.format_price(event.get('price'))}\n"
        f"15M hareket: %{radar.safe_float(event.get('move15_percent')):+.2f}\n"
        f"30M hareket: %{radar.safe_float(event.get('move30_percent')):+.2f}\n"
        f"5M hacim: {radar.safe_float(event.get('vol5')):.2f}x\n"
        f"5M RSI: {rsi5:.1f}\n"
        f"Çapraz/kalite teyidi: {confirm_text}\n"
        f"Henüz eksik olabilecek teyitler: {missing_text}"
        f"{late_warning}\n\n"
        "📌 Bu mesaj trendi kaçırmamak için erken giriş hazırlığıdır; "
        "tek başına işlem açma.\n"
        "🔄 Aynı coinde daha önce ters yön sinyali olması yeni fırsatı ENGELLEMEZ."
    )


def make_clear_signal_sender(
    original: Callable[..., Any],
) -> Callable[..., Any]:
    """Gerçek Pump/Dump sinyalini erken uyarıdan açıkça ayır."""
    if getattr(original, "_clear_pump_entry_wrapped", False):
        return original

    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        if text.startswith("🚀 PUMP/DUMP SİNYALİ"):
            text = (
                "✅ İŞLEM GİRİŞİ — PUMP/DUMP\n"
                "Giriş + TP + SL hazır. Bu, erken izleme mesajı değildir.\n\n"
                + text
            )
        return original(text, *args, **kwargs)

    wrapped._clear_pump_entry_wrapped = True  # type: ignore[attr-defined]
    return wrapped


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
            if not isinstance(event, dict):
                continue
            if not shadow_event_is_alert_worthy(radar, event):
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
            key=lambda item: (
                1 if item.get("shadow_ready") else 0,
                1 if strong_internal_trend_confirmation(radar, item) else 0,
                1
                if recent_scalp_confirmation(
                    radar,
                    str(item.get("symbol") or ""),
                    str(item.get("direction") or ""),
                )
                else 0,
                max(
                    abs(radar.safe_float(item.get("move15_percent"))),
                    abs(radar.safe_float(item.get("move30_percent"))),
                ),
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
        print("Telegram seçici büyük hareket erken uyarısı:", sent)
        return added

    wrapped._visible_big_move_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def apply_opportunity_capture(radar: Any) -> None:
    # Ters yöndeki eski açık sinyal yeni fırsatı analizden düşürmez.
    radar.has_open_same_symbol = lambda state, symbol: False
    radar.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        radar.evaluate_portfolio_risk
    )

    radar.send_telegram = make_clear_signal_sender(radar.send_telegram)
    radar.save_shadow_events = make_shadow_saver(radar, radar.save_shadow_events)


def run(radar: Any | None = None) -> None:
    if radar is None:
        import pump_radar as radar  # type: ignore[no-redef]

    apply_opportunity_capture(radar)
    print(
        "Fırsat yakalama: tüm büyük hareketler arka planda | "
        "Telegram Pump-ready / Scalp teyit / güçlü iç trend | gerçek giriş ayrı"
    )
    radar.main()


if __name__ == "__main__":
    run()
