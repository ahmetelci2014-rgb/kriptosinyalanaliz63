"""Pump/Dump canlı sinyal + seçici erken büyük hareket + trend devam giriş katmanı.

Gerçek Pump/Dump kalite şartlarını değiştirmez. Sessiz trend adaylarının tamamı
arka planda tutulur; Telegram'a yalnız Pump'ın kendi devam teyitleri tamamlanan,
Scalp aynı coin/yönü yakın zamanda işaretlemiş veya güçlü iç trend devamı oluşmuş
hareket çıkar.

Ek olarak, Pump taraması bittikten sonra güçlü iç trend yeniden güncel fiyatla
doğrulanır. Yapısal EMA20 stopu ve kontrollü risk üretilebiliyorsa ayrı
TREND_CONTINUATION gerçek işlem sinyali açılır ve mevcut Pump performans/state
altyapısıyla TP/SL takibi yapılır. Eski ters-yön sinyal yeni fırsatı bloklamaz.
Gerçek borsa emri açmaz.
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
STRONG_TREND_MIN_15M_PERCENT = 0.80
STRONG_TREND_MIN_30M_PERCENT = 1.00
STRONG_TREND_MIN_EMA20_SLOPE_PERCENT = 0.15
STRONG_TREND_MAX_EMA20_DISTANCE_PERCENT = 1.10
STRONG_TREND_MIN_5M_VOLUME_RATIO = 1.00
STRONG_TREND_LONG_RSI_MIN = 52
STRONG_TREND_LONG_RSI_MAX = 76
STRONG_TREND_SHORT_RSI_MIN = 24
STRONG_TREND_SHORT_RSI_MAX = 48

# Gerçek Trend Devam giriş yolu daha sıkıdır.
TREND_ENTRY_ENABLED = True
TREND_ENTRY_LOOKBACK_MINUTES = 12
MAX_TREND_ENTRIES_PER_RUN = 1
TREND_ENTRY_MIN_SCORE = 92
TREND_ENTRY_MIN_5M_VOLUME_RATIO = 1.30
TREND_ENTRY_MAX_EVENT_DRIFT_PERCENT = 0.30
TREND_ENTRY_MAX_15M_PERCENT = 1.60
TREND_ENTRY_MAX_30M_PERCENT = 2.50
TREND_ENTRY_MAX_EMA20_DISTANCE_PERCENT = 1.05
TREND_ENTRY_MIN_RISK_PERCENT = 0.35
TREND_ENTRY_MAX_RISK_PERCENT = 1.25
TREND_ENTRY_SL_BUFFER_PERCENT = 0.08
TREND_ENTRY_LONG_RSI_MIN = 55
TREND_ENTRY_LONG_RSI_MAX = 74
TREND_ENTRY_SHORT_RSI_MIN = 26
TREND_ENTRY_SHORT_RSI_MAX = 45
TREND_ENTRY_TP1_R = 0.75
TREND_ENTRY_TP2_R = 1.35
TREND_ENTRY_TP3_R = 2.00


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
        or price <= 0
        or ema20 <= 0
    ):
        return False

    if direction == "LONG":
        return (
            move15 >= STRONG_TREND_MIN_15M_PERCENT
            and move30 >= STRONG_TREND_MIN_30M_PERCENT
            and slope >= STRONG_TREND_MIN_EMA20_SLOPE_PERCENT
            and green_count >= 4
            and price > ema20
            and STRONG_TREND_LONG_RSI_MIN <= rsi5 <= STRONG_TREND_LONG_RSI_MAX
        )

    if direction == "SHORT":
        return (
            move15 <= -STRONG_TREND_MIN_15M_PERCENT
            and move30 <= -STRONG_TREND_MIN_30M_PERCENT
            and slope <= -STRONG_TREND_MIN_EMA20_SLOPE_PERCENT
            and red_count >= 4
            and price < ema20
            and STRONG_TREND_SHORT_RSI_MIN <= rsi5 <= STRONG_TREND_SHORT_RSI_MAX
        )

    return False


def shadow_event_is_alert_worthy(radar: Any, event: Dict[str, Any]) -> bool:
    if bool(event.get("shadow_ready")):
        return True
    if strong_internal_trend_confirmation(radar, event):
        return True
    if not movement_is_large_enough(radar, event):
        return False
    return recent_scalp_confirmation(
        radar,
        str(event.get("symbol") or ""),
        str(event.get("direction") or ""),
    ) is not None


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
        confirm_text = f"✅ SCALP {str(scalp.get('stage') or '').upper()} aynı yönde görüldü"
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
        "📌 Bu mesaj trendi kaçırmamak için erken giriş hazırlığıdır; tek başına işlem açma.\n"
        "🔄 Aynı coinde daha önce ters yön sinyali olması yeni fırsatı ENGELLEMEZ."
    )


def trend_entry_score(radar: Any, event: Dict[str, Any]) -> int:
    score = 90
    if radar.safe_float(event.get("vol5")) >= 1.50:
        score += 2
    if abs(radar.safe_float(event.get("ema20_slope_percent"))) >= 0.20:
        score += 2
    if abs(radar.safe_float(event.get("ema20_distance_percent"))) <= 0.80:
        score += 2
    if abs(radar.safe_float(event.get("move30_percent"))) >= 1.20:
        score += 2
    if abs(radar.safe_float(event.get("move15_percent"))) >= 1.00:
        score += 2
    return min(100, score)


def build_trend_entry_signal(
    radar: Any,
    event: Dict[str, Any],
    current_price: float,
) -> Optional[Dict[str, Any]]:
    if not TREND_ENTRY_ENABLED or not strong_internal_trend_confirmation(radar, event):
        return None

    direction = str(event.get("direction") or "").upper()
    event_price = radar.safe_float(event.get("price"))
    entry = radar.safe_float(current_price)
    ema20 = radar.safe_float(event.get("ema20"))
    move15 = radar.safe_float(event.get("move15_percent"))
    move30 = radar.safe_float(event.get("move30_percent"))
    vol5 = radar.safe_float(event.get("vol5"))
    rsi5 = radar.safe_float(event.get("rsi5"))
    distance = abs(radar.safe_float(event.get("ema20_distance_percent")))

    if entry <= 0 or event_price <= 0 or ema20 <= 0:
        return None

    event_drift = abs(entry - event_price) / event_price * 100
    if event_drift > TREND_ENTRY_MAX_EVENT_DRIFT_PERCENT:
        return None
    if (
        abs(move15) > TREND_ENTRY_MAX_15M_PERCENT
        or abs(move30) > TREND_ENTRY_MAX_30M_PERCENT
        or distance > TREND_ENTRY_MAX_EMA20_DISTANCE_PERCENT
        or vol5 < TREND_ENTRY_MIN_5M_VOLUME_RATIO
    ):
        return None

    if direction == "LONG":
        if not (entry > ema20 and TREND_ENTRY_LONG_RSI_MIN <= rsi5 <= TREND_ENTRY_LONG_RSI_MAX):
            return None
        sl = ema20 * (1 - TREND_ENTRY_SL_BUFFER_PERCENT / 100)
        risk = entry - sl
    elif direction == "SHORT":
        if not (entry < ema20 and TREND_ENTRY_SHORT_RSI_MIN <= rsi5 <= TREND_ENTRY_SHORT_RSI_MAX):
            return None
        sl = ema20 * (1 + TREND_ENTRY_SL_BUFFER_PERCENT / 100)
        risk = sl - entry
    else:
        return None

    if risk <= 0:
        return None
    risk_percent = risk / entry * 100
    if not (TREND_ENTRY_MIN_RISK_PERCENT <= risk_percent <= TREND_ENTRY_MAX_RISK_PERCENT):
        return None

    score = trend_entry_score(radar, event)
    if score < TREND_ENTRY_MIN_SCORE:
        return None

    if direction == "LONG":
        tp1 = entry + risk * TREND_ENTRY_TP1_R
        tp2 = entry + risk * TREND_ENTRY_TP2_R
        tp3 = entry + risk * TREND_ENTRY_TP3_R
    else:
        tp1 = entry - risk * TREND_ENTRY_TP1_R
        tp2 = entry - risk * TREND_ENTRY_TP2_R
        tp3 = entry - risk * TREND_ENTRY_TP3_R

    return {
        "symbol": radar.normalize_bot_symbol(event.get("symbol")),
        "direction": direction,
        "source": "TREND_CONTINUATION",
        "setup_name": f"Güçlü Trend Devam {direction}",
        "entry": entry,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl": sl,
        "score": score,
        "risk_percent": risk_percent,
        "move1": 0.0,
        "move5": 0.0,
        "move15": move15,
        "rsi1": 0.0,
        "rsi5": rsi5,
        "vol1": radar.safe_float(event.get("vol1")),
        "vol5": vol5,
        "lower_wick": 0.0,
        "upper_wick": 0.0,
        "close_power": 0.0,
        "trend_event_price": event_price,
        "trend_event_drift_percent": event_drift,
        "ema20": ema20,
        "ema20_slope_percent": radar.safe_float(event.get("ema20_slope_percent")),
        "ema20_distance_percent": distance,
        "move30": move30,
    }


def build_trend_entry_message(radar: Any, signal: Dict[str, Any]) -> str:
    icon = "🟢" if signal["direction"] == "LONG" else "🔴"
    return (
        "🚀 TREND DEVAM SİNYALİ\n\n"
        f"{icon} {signal['direction']} | {signal['symbol']}\n"
        "📌 Kurulum: Güçlü trend + devam teyidi + yapısal stop\n"
        f"💰 Giriş: {radar.format_price(signal['entry'])}\n"
        f"🎯 TP1: {radar.format_price(signal['tp1'])}\n"
        f"🎯 TP2: {radar.format_price(signal['tp2'])}\n"
        f"🎯 TP3: {radar.format_price(signal['tp3'])}\n"
        f"🛑 SL: {radar.format_price(signal['sl'])}\n"
        f"⭐ Skor: {signal['score']}/100\n"
        f"📏 Stop mesafesi: %{signal['risk_percent']:.2f}\n"
        f"📊 15M/30M: %{signal['move15']:+.2f} / %{signal['move30']:+.2f}\n"
        f"📈 5M hacim: {signal['vol5']:.2f}x | RSI: {signal['rsi5']:.1f}\n\n"
        "✅ Bu erken izleme değil; giriş + TP + SL üretilmiş gerçek işlem adayıdır.\n"
        "⚠️ Fiyat girişten belirgin uzaklaşmışsa peşinden koşma."
    )


def make_clear_signal_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(original, "_clear_pump_entry_wrapped", False):
        return original

    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        if text.startswith("🚀 TREND DEVAM SİNYALİ"):
            text = (
                "✅ İŞLEM GİRİŞİ — TREND DEVAM\n"
                "Giriş + TP + SL hazır. Bu, erken izleme mesajı değildir.\n\n" + text
            )
        elif text.startswith("🚀 PUMP/DUMP SİNYALİ"):
            text = (
                "✅ İŞLEM GİRİŞİ — PUMP/DUMP\n"
                "Giriş + TP + SL hazır. Bu, erken izleme mesajı değildir.\n\n" + text
            )
        return original(text, *args, **kwargs)

    wrapped._clear_pump_entry_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def make_shadow_saver(radar: Any, original: Callable[..., int]) -> Callable[..., int]:
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
            key=lambda item: (
                1 if item.get("shadow_ready") else 0,
                1 if strong_internal_trend_confirmation(radar, item) else 0,
                1 if recent_scalp_confirmation(
                    radar,
                    str(item.get("symbol") or ""),
                    str(item.get("direction") or ""),
                ) else 0,
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


def latest_strong_trend_events(radar: Any, state: Dict[str, Any]) -> List[Dict[str, Any]]:
    cutoff = radar.now_ts() - TREND_ENTRY_LOOKBACK_MINUTES * 60
    latest: Dict[str, Dict[str, Any]] = {}
    for event in state.get("shadow_moves", []) or []:
        if not isinstance(event, dict):
            continue
        recorded_at = int(event.get("recorded_at") or 0)
        if recorded_at < cutoff or not strong_internal_trend_confirmation(radar, event):
            continue
        key = f"{event.get('symbol')}_{event.get('direction')}"
        previous = latest.get(key)
        if previous is None or recorded_at > int(previous.get("recorded_at") or 0):
            latest[key] = event

    return sorted(
        latest.values(),
        key=lambda event: (
            trend_entry_score(radar, event),
            abs(radar.safe_float(event.get("move30_percent"))),
        ),
        reverse=True,
    )


def run_trend_continuation_entries(radar: Any) -> int:
    if not TREND_ENTRY_ENABLED:
        return 0

    state = radar.load_state()
    open_count = sum(
        1 for signal in state.get("open_signals", {}).values()
        if isinstance(signal, dict) and not bool(signal.get("closed"))
    )
    available_slots = max(0, int(radar.MAX_OPEN_SIGNALS) - open_count)
    max_to_send = min(MAX_TREND_ENTRIES_PER_RUN, available_slots)
    if max_to_send <= 0:
        print("Trend devam girişi: açık Pump/Dump limiti dolu.")
        return 0

    events = latest_strong_trend_events(radar, state)
    if not events:
        print("Trend devam girişi: uygun güçlü iç trend yok.")
        return 0

    exchange = radar.get_exchange()
    sent = 0
    for event in events:
        if sent >= max_to_send:
            break

        symbol = radar.normalize_bot_symbol(event.get("symbol"))
        direction = str(event.get("direction") or "").upper()
        if radar.is_recent_duplicate(state, symbol, direction):
            continue

        current_price = radar.get_current_price(exchange, symbol)
        signal = build_trend_entry_signal(radar, event, radar.safe_float(current_price))
        if signal is None:
            continue

        portfolio_risk = radar.evaluate_portfolio_risk(
            symbol=signal["symbol"],
            direction=signal["direction"],
            source_bot="PUMP_DUMP",
        )
        if portfolio_risk.get("hard_block", False):
            print(
                signal["symbol"],
                "trend devam portföy koruması nedeniyle elendi:",
                portfolio_risk.get("block_code") or portfolio_risk.get("reason"),
            )
            continue

        portfolio_note = str(radar.format_portfolio_note(portfolio_risk) or "").strip()
        message = build_trend_entry_message(radar, signal)
        if portfolio_note:
            message += "\n" + portfolio_note

        delivery_bucket = radar.now_ts() // max(1, int(radar.DUPLICATE_SECONDS))
        if not radar.send_telegram(
            message,
            delivery_key=(
                f"TREND_CONTINUATION|{signal['symbol']}|"
                f"{signal['direction']}|{delivery_bucket}"
            ),
        ):
            continue

        record_id = radar.record_pump_performance(signal)
        if record_id:
            signal["performance_record_id"] = record_id
        radar.save_open_signal(state, signal)
        radar.mark_sent(state, signal["symbol"], signal["direction"])
        radar.increment_stat(state, "signals")
        radar.save_state(state)
        sent += 1

        print(
            "Trend devam gerçek giriş:",
            signal["symbol"],
            signal["direction"],
            "skor", signal["score"],
            "risk", round(signal["risk_percent"], 3),
        )

    return sent


def apply_opportunity_capture(radar: Any) -> None:
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
        "Telegram Pump-ready / Scalp teyit / güçlü iç trend | "
        "Trend Devam gerçek giriş yolu AKTİF"
    )
    radar.main()
    sent = run_trend_continuation_entries(radar)
    print("Trend devam gerçek giriş sayısı:", sent)


if __name__ == "__main__":
    run()
