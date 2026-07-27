# main.py
# Premium MTF Futures Bot - Akilli Takip v3 + Net R + Teknik Teshis
#
# GitHub Actions icin Telegram sinyal botu.
# Emir acmaz. Sinyal gonderir, TP/SL takip eder.
#
# v3 eklemeleri:
# - Eski performance.json raporu korunur.
# - Her sinyal trade_ledger.json icinde tekil trade_id ile tutulur.
# - TP1 / TP2 / TP3 olaylari ayni islemin olaylari olarak kaydedilir.
# - Her islem tek nihai sonuc ve net R degeriyle kapanir.
# - TP1'in ilk goruldugu ayni mumda yanlis BE kapanisi engellenir.
# - Telegram API yanit govdesi loglanmaz; yalnizca HTTP kodu yazilir.
# - Yeni sinyallerin trend, hacim, giris uzakligi ve hareket profili kaydedilir.
# - Kapanan islemlere kayitli verilere dayali olasilik temelli teknik teshis eklenir.
# - Stop sonrasi TP1'e donen islemlerde fitil / dar stop olasiligi isaretlenir.
# - Süresi dolan işlemler 24 saat daha sessiz izlenir; hedef/koruma sırası kaydedilir.
# - Günlük Telegram raporu tekleştirildi: Net R + tüm teşhisler v5.
# - Stoplar olasılık temelli kök nedenlere ayrılır; işlem kuralları değişmez.

import json
import os
import time
from datetime import datetime, timedelta, timezone

import ccxt
import pandas as pd
import requests

from config import (
    BOT_NAME,
    SYSTEM_NOTE,
    AUTO_TOP_VOLUME_SCAN,
    MAX_SCAN_COINS,
    MIN_24H_QUOTE_VOLUME,
    PRIORITY_COINS,
    ALLOW_LONG,
    ALLOW_SHORT,
    MAX_TRADE_SIGNALS_PER_RUN,
    MAX_RADAR_ALERTS_PER_RUN,
    MAX_OPEN_SIGNALS,
    RISK_MODE_STOP_COUNT,
    RISK_MODE_MAX_TRADE_SIGNALS,
    RISK_MODE_MAX_RADAR_ALERTS,
    RISK_MODE_ALLOW_RADAR_TRADE,
    RADAR_TIMEFRAME,
    ENTRY_TIMEFRAME,
    CONFIRM_TIMEFRAME,
    TREND_TIMEFRAME,
    TRACK_TIMEFRAME,
    RADAR_LIMIT,
    ENTRY_LIMIT,
    CONFIRM_LIMIT,
    TREND_LIMIT,
    TRACK_LIMIT,
    MAX_ENTRY_DISTANCE_PERCENT,
    MAX_TP1_PROGRESS_PERCENT,
    MARKET_GUARD_ENABLED,
    MARKET_REFERENCE_COINS,
    MARKET_LONG_MIN_OK_COUNT,
    MARKET_SHORT_MIN_OK_COUNT,
    MARKET_MAX_COUNTER_5M_MOVE_PERCENT,
    TRADE_DUPLICATE_BLOCK_SECONDS,
    RADAR_DUPLICATE_BLOCK_SECONDS,
    STOPPED_COIN_COOLDOWN_HOURS,
    MAX_OPEN_SIGNAL_HOURS,
    SEND_STATUS_EVERY_MINUTES,
    OPEN_SUMMARY_EVERY_MINUTES,
    DAILY_REPORT_HOUR,
    DAILY_REPORT_MINUTE,
)

from strategy import (
    analyze_mtf_trade,
    analyze_5m_radar,
    format_price,
)


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

OPEN_SIGNALS_FILE = "open_signals.json"
PERFORMANCE_FILE = "performance.json"
LAST_SIGNALS_FILE = "last_signals.json"
TRADE_LEDGER_FILE = "trade_ledger.json"

TR_TIMEZONE = timezone(timedelta(hours=3))

# Stop sonrası takip: 30 / 60 / 120 / 180 / 240 dakika.
# 1M mum kullanıldığında 240 dakikanın tamamını görebilmek için
# ana TRACK_LIMIT değerinden bağımsız daha geniş veri limiti kullanılır.
SL_AFTER_CHECKPOINT_MINUTES = [30, 60, 120, 180, 240]
SL_AFTER_MAX_TRACK_MINUTES = 240
SL_AFTER_TRACK_LIMIT = 300

# Süresi dolan işlemler Telegram takibinden çıkarılır; ancak
# yazılım yönün daha sonra hedefe mi yoksa koruma seviyesine mi
# gittiğini 24 saat boyunca sessizce ölçmeye devam eder.
POST_EXPIRY_CHECKPOINT_HOURS = [6, 12, 24]
POST_EXPIRY_MAX_TRACK_HOURS = 24
POST_EXPIRY_RESTORE_MAX_HOURS = 48
POST_EXPIRY_TIMEFRAME = "5m"
POST_EXPIRY_TRACK_LIMIT = 320

# Stop kök neden sınıflandırma eşikleri yalnız teşhis içindir.
# Sinyal üretimi, TP/SL veya işlem filtrelerini değiştirmez.
STOP_CAUSE_QUICK_MINUTES = 30
STOP_CAUSE_EARLY_ENTRY_MINUTES = 60
STOP_CAUSE_LOW_MFE_R = 0.15
STOP_CAUSE_MEANINGFUL_MFE_R = 0.35
STOP_CAUSE_FAR_ENTRY_PERCENT = 0.25
STOP_CAUSE_WEAK_VOLUME_RATIO = 0.90
STOP_CAUSE_WEAK_ADX = 18.0
STOP_CAUSE_TIGHT_RISK_PERCENT = 0.85

RECENT_CLOSED_COIN_COOLDOWN_SECONDS = 4 * 60 * 60


# =========================================================
# GENEL YARDIMCILAR
# =========================================================

def now_ts():
    return int(time.time())


def today_key():
    return datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d")


def day_key_from_ts(timestamp):
    try:
        return datetime.fromtimestamp(
            int(timestamp),
            TR_TIMEZONE,
        ).strftime("%Y-%m-%d")
    except Exception:
        return today_key()


def clock_from_ts(timestamp):
    try:
        return datetime.fromtimestamp(
            int(timestamp),
            TR_TIMEZONE,
        ).strftime("%H:%M:%S")
    except Exception:
        return "--:--:--"


def safe_float(value, default=None):
    try:
        if value in (None, "", "-"):
            return default

        number = float(value)

        if number != number:
            return default

        return number

    except Exception:
        return default


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik.")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": str(message),
            },
            timeout=20,
        )

        print("Telegram cevap:", response.status_code)

        return response.status_code == 200

    except Exception as exc:
        print("Telegram gönderim hatası:", exc)
        return False


def load_json_file(filename, default=None):
    if default is None:
        default = {}

    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as handle:
            content = handle.read().strip()

        if not content:
            return default

        data = json.loads(content)
        return data if isinstance(data, dict) else default

    except Exception as exc:
        print(filename, "okuma hatası:", exc)
        return default


def save_json_file(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as handle:
            json.dump(
                data if isinstance(data, dict) else {},
                handle,
                indent=2,
                ensure_ascii=False,
            )

        return True

    except Exception as exc:
        print(filename, "kaydetme hatası:", exc)
        return False


def load_open_signals():
    return load_json_file(OPEN_SIGNALS_FILE)


def save_open_signals(data):
    return save_json_file(OPEN_SIGNALS_FILE, data)


def load_performance():
    return load_json_file(PERFORMANCE_FILE)


def save_performance(data):
    return save_json_file(PERFORMANCE_FILE, data)


def load_last_signals():
    return load_json_file(LAST_SIGNALS_FILE)


def save_last_signals(data):
    return save_json_file(LAST_SIGNALS_FILE, data)


# =========================================================
# NET R TRADE LEDGER
# =========================================================

def empty_trade_ledger():
    return {
        "trades": {},
        "last_update": 0,
    }


def load_trade_ledger():
    ledger = load_json_file(
        TRADE_LEDGER_FILE,
        empty_trade_ledger(),
    )

    ledger.setdefault("trades", {})
    ledger.setdefault("last_update", 0)

    if not isinstance(ledger["trades"], dict):
        ledger["trades"] = {}

    return ledger


def save_trade_ledger(ledger):
    ledger["last_update"] = now_ts()
    return save_json_file(TRADE_LEDGER_FILE, ledger)


def build_trade_id(signal):
    existing = str(
        signal.get("trade_id")
        or ""
    ).strip()

    if existing:
        return existing

    opened_at = int(
        signal.get("opened_at")
        or now_ts()
    )

    return (
        f"{signal.get('symbol', 'UNKNOWN')}_"
        f"{signal.get('direction', 'UNKNOWN')}_"
        f"{signal.get('source', 'MTF')}_"
        f"{opened_at}"
    )


def ledger_target_r(trade, target_key):
    entry = safe_float(trade.get("entry"))
    sl = safe_float(trade.get("sl"))
    target = safe_float(trade.get(target_key))

    if entry is None or sl is None or target is None:
        return None

    risk = abs(entry - sl)

    if risk <= 0:
        return None

    return abs(target - entry) / risk


def calculate_exit_r(signal, exit_price):
    entry = safe_float(signal.get("entry"))
    sl = safe_float(signal.get("sl"))
    price = safe_float(exit_price)

    if entry is None or sl is None or price is None:
        return None

    risk = abs(entry - sl)

    if risk <= 0:
        return None

    direction = str(
        signal.get("direction", "")
    ).upper()

    if direction == "LONG":
        remaining_r = (
            price - entry
        ) / risk

    elif direction == "SHORT":
        remaining_r = (
            entry - price
        ) / risk

    else:
        return None

    # TP1 geldiyse pozisyonun %50'si TP1'de alınmış,
    # kalan %50 süre dolduğu andaki fiyatla hesaplanır.
    if bool(signal.get("tp1_hit", False)):
        tp1 = safe_float(signal.get("tp1"))

        if tp1 is None:
            return None

        if direction == "LONG":
            tp1_r = (
                tp1 - entry
            ) / risk
        else:
            tp1_r = (
                entry - tp1
            ) / risk

        return round(
            0.50 * tp1_r
            + 0.50 * remaining_r,
            4,
        )

    return round(remaining_r, 4)



def signal_diagnostic_snapshot(signal):
    """
    Strategy tarafinda uretilen sinyal anlik verilerini tek yerde toplar.
    Eski sinyallerde bulunmayan alanlar None olarak kalir.
    """
    return {
        "quality": signal.get("quality"),
        "quality_note": signal.get("quality_note"),
        "trend_reason": signal.get("trend_reason"),
        "confirm_reason": signal.get("confirm_reason"),
        "entry_reason": signal.get("entry_reason"),
        "radar_reason": signal.get("radar_reason"),
        "rsi_15m": safe_float(signal.get("rsi_15m")),
        "adx_15m": safe_float(signal.get("adx_15m")),
        "adx_4h": safe_float(signal.get("adx_4h")),
        "adx_1h": safe_float(signal.get("adx_1h")),
        "volume_ratio": safe_float(signal.get("volume_ratio")),
        "ideal_entry": safe_float(signal.get("ideal_entry")),
        "zone_name": signal.get("zone_name"),
        "zone_distance_percent": safe_float(
            signal.get("zone_distance_percent")
        ),
        "rr_tp1": safe_float(signal.get("rr_tp1")),
        "rr_tp2": safe_float(signal.get("rr_tp2")),
        "rr_tp3": safe_float(signal.get("rr_tp3")),
        "leverage": signal.get("leverage"),
        "sent_price": safe_float(signal.get("sent_price")),
        "entry_distance_at_send_percent": safe_float(
            signal.get("entry_distance_at_send_percent")
        ),
        "tp1_progress_at_send_percent": safe_float(
            signal.get("tp1_progress_at_send_percent")
        ),
        "market_guard_long_allowed": signal.get(
            "market_guard_long_allowed"
        ),
        "market_guard_short_allowed": signal.get(
            "market_guard_short_allowed"
        ),
        "market_guard_reason": signal.get("market_guard_reason"),
    }


def apply_signal_tracking_to_trade(trade, signal):
    snapshot = signal_diagnostic_snapshot(signal)

    for key, value in snapshot.items():
        if value is not None:
            trade[key] = value

    tracking_fields = (
        "best_favorable_percent",
        "worst_adverse_percent",
        "best_favorable_r",
        "worst_adverse_r",
        "best_favorable_price",
        "worst_adverse_price",
        "last_market_price",
        "last_tracking_at",
        "tp1_hit_at",
    )

    for key in tracking_fields:
        value = signal.get(key)

        if value is not None:
            trade[key] = value

    trade["tp1_hit"] = bool(
        signal.get("tp1_hit", trade.get("tp1_hit", False))
    )
    trade["tp2_hit"] = bool(
        signal.get("tp2_hit", trade.get("tp2_hit", False))
    )
    trade["tp3_hit"] = bool(
        signal.get("tp3_hit", trade.get("tp3_hit", False))
    )


def update_signal_excursion(signal, high, low, candle_time=None):
    """
    Sinyal acildiktan sonra gorulen en iyi lehe ve en kotu ters hareketi
    yuzde ve R cinsinden kaydeder.
    """
    entry = safe_float(signal.get("entry"))
    sl = safe_float(signal.get("sl"))
    high = safe_float(high)
    low = safe_float(low)

    if (
        entry is None
        or sl is None
        or high is None
        or low is None
        or entry <= 0
    ):
        return

    risk = abs(entry - sl)
    direction = str(signal.get("direction", "")).upper()

    if direction == "LONG":
        favorable_price = high
        adverse_price = low
        favorable_percent = max(
            0.0,
            (high - entry) / entry * 100,
        )
        adverse_percent = max(
            0.0,
            (entry - low) / entry * 100,
        )
        favorable_r = (
            max(0.0, (high - entry) / risk)
            if risk > 0
            else 0.0
        )
        adverse_r = (
            max(0.0, (entry - low) / risk)
            if risk > 0
            else 0.0
        )

    elif direction == "SHORT":
        favorable_price = low
        adverse_price = high
        favorable_percent = max(
            0.0,
            (entry - low) / entry * 100,
        )
        adverse_percent = max(
            0.0,
            (high - entry) / entry * 100,
        )
        favorable_r = (
            max(0.0, (entry - low) / risk)
            if risk > 0
            else 0.0
        )
        adverse_r = (
            max(0.0, (high - entry) / risk)
            if risk > 0
            else 0.0
        )

    else:
        return

    if favorable_percent > safe_float(
        signal.get("best_favorable_percent"),
        0.0,
    ):
        signal["best_favorable_percent"] = round(
            favorable_percent,
            4,
        )
        signal["best_favorable_r"] = round(
            favorable_r,
            4,
        )
        signal["best_favorable_price"] = favorable_price

    if adverse_percent > safe_float(
        signal.get("worst_adverse_percent"),
        0.0,
    ):
        signal["worst_adverse_percent"] = round(
            adverse_percent,
            4,
        )
        signal["worst_adverse_r"] = round(
            adverse_r,
            4,
        )
        signal["worst_adverse_price"] = adverse_price

    signal["last_tracking_at"] = int(
        candle_time or now_ts()
    )


def build_trade_diagnosis(trade):
    """
    Bu sonuc kesin piyasa sebebi degildir.
    Kayitli teknik verilere dayali olasilik temelli siniflandirmadir.
    """
    result = str(
        trade.get("final_result") or ""
    ).upper()

    duration_minutes = int(
        max(
            0,
            (
                int(trade.get("closed_at") or now_ts())
                - int(trade.get("opened_at") or now_ts())
            )
            / 60,
        )
    )

    mfe_r = safe_float(
        trade.get("best_favorable_r"),
        0.0,
    )
    mae_r = safe_float(
        trade.get("worst_adverse_r"),
        0.0,
    )
    volume_ratio = safe_float(
        trade.get("volume_ratio")
    )
    adx_4h = safe_float(trade.get("adx_4h"))
    adx_1h = safe_float(trade.get("adx_1h"))
    adx_15m = safe_float(trade.get("adx_15m"))
    zone_distance = safe_float(
        trade.get("zone_distance_percent")
    )
    entry_distance = safe_float(
        trade.get("entry_distance_at_send_percent")
    )
    source = str(trade.get("source", ""))

    factors = []
    primary = "KAYIT YETERSIZ"
    confidence = "DUSUK"

    if result == "TP3":
        primary = "KURULUM BASARILI"
        confidence = "YUKSEK"
        factors.append(
            "Sinyal maksimum hedef TP3'e ulasti."
        )

    elif result in {
        "TP1_SONRASI_BE",
        "TP2_SONRASI_BE",
    }:
        primary = "YON DOGRU, DEVAM GUCU ZAYIFLADI"
        confidence = "YUKSEK"
        factors.append(
            "Islem hedef gordukten sonra kalan kisim giristen kapandi."
        )

    elif result == "EXPIRED":
        r_result = safe_float(trade.get("r_result"))

        if r_result is not None and r_result > 0:
            primary = "YON KISMEN DOGRU, HEDEF TAMAMLANMADI"
        elif r_result is not None and r_result < 0:
            primary = "YON DEVAM ETMEDI / ZAMAN ASIMI"
        else:
            primary = "BELIRGIN SONUC OLUSMADI"

        confidence = "ORTA"
        factors.append(
            "Islem belirlenen takip suresi icinde TP3 veya SL ile kapanmadi."
        )

    elif result == "SL":
        confidence = "ORTA"

        if duration_minutes <= 20 and mfe_r < 0.15:
            primary = "HIZLI TERS HAREKET / YON UYUMSUZLUGU"
            factors.append(
                "Islem ilk 20 dakikada anlamli lehe hareket yapmadan stop oldu."
            )

        elif mfe_r >= 0.35:
            primary = "ONCE LEHE GITTI, SONRA TERS DONDU"
            factors.append(
                "Stop oncesinde islem en az 0.35R lehe hareket etti."
            )

        else:
            primary = "KURULUM DEVAM ETMEDI"
            factors.append(
                "Islem yeterli lehe ivme olusturmadan stop oldu."
            )

        if (
            zone_distance is not None
            and zone_distance > 0.25
        ):
            factors.append(
                "Giris ideal bolgeden goreceli olarak uzakti."
            )

        if (
            entry_distance is not None
            and entry_distance > 0.25
        ):
            factors.append(
                "Telegram gonderim aninda fiyat giristen uzaklasmisti."
            )

        if (
            volume_ratio is not None
            and volume_ratio < 0.90
        ):
            factors.append(
                "15M hacim kendi ortalamasinin altinda veya sinirdaydi."
            )

        weak_adx = [
            value
            for value in (adx_4h, adx_1h)
            if value is not None
        ]

        if weak_adx and min(weak_adx) < 18:
            factors.append(
                "Ust zaman dilimi trend gucu sinirdaydi."
            )

        if (
            source == "5M_RADAR"
            and adx_15m is not None
            and adx_15m < 18
        ):
            factors.append(
                "5M erken giriste 15M trend gucu sinirdaydi."
            )

        if mae_r >= 1.0 and duration_minutes <= 20:
            factors.append(
                "Stop mesafesi cok kisa surede tamamen tuketildi."
            )

    return {
        "version": "TECH_DIAGNOSIS_V1",
        "primary": primary,
        "confidence": confidence,
        "factors": factors,
        "duration_minutes": duration_minutes,
        "best_favorable_r": round(mfe_r, 4),
        "worst_adverse_r": round(mae_r, 4),
        "provisional": result == "SL",
        "note": (
            "Bu teshis kesin piyasa sebebi degil; "
            "kayitli teknik verilere dayali olasilik temelli degerlendirmedir."
        ),
    }



# =========================================================
# STOP KÖK NEDEN SINIFLANDIRMASI
# =========================================================

STOP_ROOT_CAUSE_LABELS = {
    "TAKIP_SURUYOR":
        "Takip sürüyor",
    "FITIL_DAR_STOP":
        "Fitil/dar stop",
    "MUHTEMEL_ERKEN_GIRIS":
        "Muhtemel erken giriş",
    "ERKEN_GIRIS_VEYA_DAR_STOP":
        "Erken giriş veya dar stop",
    "MUHTEMEL_YANLIS_YON":
        "Muhtemel yanlış yön",
    "ZAYIF_TREND_HACIM":
        "Zayıf trend/hacim",
    "GEC_UZAK_GIRIS":
        "Geç/uzak giriş",
    "ONCE_LEHE_SONRA_TERS":
        "Önce lehe, sonra ters",
    "KURULUM_DEVAM_ETMEDI":
        "Kurulum devam etmedi",
    "VERI_YETERSIZ":
        "Veri yetersiz",
}


def classify_stop_root_cause(trade):
    """
    Stopun muhtemel kök nedenini kayıtlı verilerden sınıflandırır.

    Bu fonksiyon kesin piyasa sebebi iddia etmez. Özellikle erken giriş
    ile dar stop aynı fiyat davranışını gösterebildiği için kanıt
    yetersiz olduğunda birleşik kategori kullanılır.
    """
    if str(
        trade.get("final_result", "")
    ).upper() != "SL":
        return None

    duration_minutes = int(
        safe_float(
            trade.get("duration_minutes"),
            0,
        )
        or 0
    )
    mfe_r = safe_float(
        trade.get("best_favorable_r"),
        0.0,
    )
    mae_r = safe_float(
        trade.get("worst_adverse_r"),
        0.0,
    )
    risk_percent = safe_float(
        trade.get("risk_percent")
    )
    volume_ratio = safe_float(
        trade.get("volume_ratio")
    )
    adx_4h = safe_float(
        trade.get("adx_4h")
    )
    adx_1h = safe_float(
        trade.get("adx_1h")
    )
    adx_15m = safe_float(
        trade.get("adx_15m")
    )
    zone_distance = safe_float(
        trade.get("zone_distance_percent")
    )
    entry_distance = safe_float(
        trade.get(
            "entry_distance_at_send_percent"
        )
    )
    source = str(
        trade.get("source", "")
    ).upper()

    follow = (
        trade.get("post_stop_follow")
        or {}
    )
    follow_status = str(
        follow.get("status", "")
    ).upper()
    returned_level = str(
        follow.get("returned_level", "")
        or ""
    ).upper()
    return_minutes = safe_float(
        follow.get("age_minutes")
    )

    weak_signals = []
    factors = []
    secondary = []

    if (
        volume_ratio is not None
        and volume_ratio
        < STOP_CAUSE_WEAK_VOLUME_RATIO
    ):
        weak_signals.append("HACIM")
        factors.append(
            "15M hacim oranı sınırın altındaydı."
        )

    upper_adx_values = [
        value
        for value in (adx_4h, adx_1h)
        if value is not None
    ]

    if (
        upper_adx_values
        and min(upper_adx_values)
        < STOP_CAUSE_WEAK_ADX
    ):
        weak_signals.append("UST_ZAMAN_ADX")
        factors.append(
            "Üst zaman dilimi trend gücü sınırdaydı."
        )

    if (
        source == "5M_RADAR"
        and adx_15m is not None
        and adx_15m < STOP_CAUSE_WEAK_ADX
    ):
        weak_signals.append("15M_ADX")
        factors.append(
            "5M erken girişte 15M trend gücü sınırdaydı."
        )

    far_entry = bool(
        (
            zone_distance is not None
            and zone_distance
            > STOP_CAUSE_FAR_ENTRY_PERCENT
        )
        or (
            entry_distance is not None
            and entry_distance
            > STOP_CAUSE_FAR_ENTRY_PERCENT
        )
    )

    if far_entry:
        factors.append(
            "Giriş ideal bölgeden veya gönderim fiyatından uzaktı."
        )
        secondary.append("GEC_UZAK_GIRIS")

    quick_low_mfe = bool(
        duration_minutes
        <= STOP_CAUSE_QUICK_MINUTES
        and mfe_r < STOP_CAUSE_LOW_MFE_R
    )

    early_entry_profile = bool(
        duration_minutes
        <= STOP_CAUSE_EARLY_ENTRY_MINUTES
        and mfe_r < STOP_CAUSE_LOW_MFE_R
        and (
            source == "5M_RADAR"
            or bool(weak_signals)
        )
    )

    tight_stop_profile = bool(
        risk_percent is not None
        and risk_percent
        <= STOP_CAUSE_TIGHT_RISK_PERCENT
    )

    # 240 dakikalık takip henüz bitmediyse kesin kök neden verilmez.
    if follow_status not in {
        "RETURNED_TO_TARGET",
        "NO_TP1_RETURN",
    }:
        preliminary = None

        if quick_low_mfe:
            preliminary = "MUHTEMEL_YANLIS_YON"
        elif far_entry:
            preliminary = "GEC_UZAK_GIRIS"
        elif weak_signals:
            preliminary = "ZAYIF_TREND_HACIM"
        elif mfe_r >= STOP_CAUSE_MEANINGFUL_MFE_R:
            preliminary = "ONCE_LEHE_SONRA_TERS"
        else:
            preliminary = "KURULUM_DEVAM_ETMEDI"

        factors.insert(
            0,
            "Stop sonrası 240 dakikalık takip henüz tamamlanmadı.",
        )

        return {
            "version": "STOP_ROOT_CAUSE_V1",
            "primary": "TAKIP_SURUYOR",
            "label": STOP_ROOT_CAUSE_LABELS[
                "TAKIP_SURUYOR"
            ],
            "preliminary": preliminary,
            "preliminary_label": (
                STOP_ROOT_CAUSE_LABELS.get(
                    preliminary
                )
            ),
            "secondary": list(dict.fromkeys(secondary)),
            "confidence": "DUSUK",
            "provisional": True,
            "factors": factors,
            "metrics": {
                "duration_minutes": duration_minutes,
                "mfe_r": round(mfe_r, 4),
                "mae_r": round(mae_r, 4),
                "risk_percent": risk_percent,
                "return_minutes": return_minutes,
            },
        }

    # Stop sonrası hedefe dönüş: yön tamamen yanlış değildir.
    if follow_status == "RETURNED_TO_TARGET":
        factors.insert(
            0,
            (
                f"Stop sonrası fiyat {returned_level or 'TP1'} "
                f"seviyesine {int(return_minutes or 0)} dakikada döndü."
            ),
        )

        if early_entry_profile and tight_stop_profile:
            primary = "ERKEN_GIRIS_VEYA_DAR_STOP"
            confidence = "ORTA"
            factors.append(
                "Hızlı ve düşük MFE'li stop ile dar risk profili birlikte görüldü."
            )

        elif early_entry_profile:
            primary = "MUHTEMEL_ERKEN_GIRIS"
            confidence = "ORTA"
            factors.append(
                "İşlem kısa sürede düşük lehe hareketle stop oldu, sonra hedefe döndü."
            )

        else:
            primary = "FITIL_DAR_STOP"
            confidence = "YUKSEK"

            if tight_stop_profile:
                factors.append(
                    "Stop yüzdesi teşhis eşiğine göre dardı."
                )
            else:
                factors.append(
                    "Stop sonrası hedef dönüşü fitil veya giriş zamanlaması ihtimalini güçlendirdi."
                )

        return {
            "version": "STOP_ROOT_CAUSE_V1",
            "primary": primary,
            "label": STOP_ROOT_CAUSE_LABELS[primary],
            "preliminary": None,
            "preliminary_label": None,
            "secondary": list(dict.fromkeys(secondary)),
            "confidence": confidence,
            "provisional": False,
            "factors": factors,
            "metrics": {
                "duration_minutes": duration_minutes,
                "mfe_r": round(mfe_r, 4),
                "mae_r": round(mae_r, 4),
                "risk_percent": risk_percent,
                "return_minutes": return_minutes,
            },
        }

    # 240 dakikada hedefe dönüş yok: stop daha çok kurulum/yön kaynaklıdır.
    factors.insert(
        0,
        (
            f"Stop sonrası {SL_AFTER_MAX_TRACK_MINUTES} dakika "
            f"içinde TP1'e dönüş olmadı."
        ),
    )

    if far_entry:
        primary = "GEC_UZAK_GIRIS"
        confidence = "ORTA"

    elif quick_low_mfe:
        primary = "MUHTEMEL_YANLIS_YON"
        confidence = "ORTA"
        factors.append(
            "İşlem çok kısa sürede anlamlı lehe hareket üretmeden stop oldu."
        )

    elif (
        weak_signals
        and mfe_r < STOP_CAUSE_MEANINGFUL_MFE_R
    ):
        primary = "ZAYIF_TREND_HACIM"
        confidence = "ORTA"

    elif mfe_r >= STOP_CAUSE_MEANINGFUL_MFE_R:
        primary = "ONCE_LEHE_SONRA_TERS"
        confidence = "YUKSEK"
        factors.append(
            "İşlem stop öncesinde anlamlı lehe hareket üretmişti."
        )

    else:
        primary = "KURULUM_DEVAM_ETMEDI"
        confidence = "ORTA"
        factors.append(
            "İşlem yeterli lehe ivme üretmeden stop oldu."
        )

    return {
        "version": "STOP_ROOT_CAUSE_V1",
        "primary": primary,
        "label": STOP_ROOT_CAUSE_LABELS[primary],
        "preliminary": None,
        "preliminary_label": None,
        "secondary": list(dict.fromkeys(secondary)),
        "confidence": confidence,
        "provisional": False,
        "factors": factors,
        "metrics": {
            "duration_minutes": duration_minutes,
            "mfe_r": round(mfe_r, 4),
            "mae_r": round(mae_r, 4),
            "risk_percent": risk_percent,
            "return_minutes": return_minutes,
        },
    }


def update_trade_stop_root_cause(trade):
    classified = classify_stop_root_cause(trade)

    if classified is None:
        return False

    current = (
        trade.get("stop_root_cause")
        or {}
    )

    comparable_current = {
        key: value
        for key, value in current.items()
        if key != "updated_at"
    }

    if comparable_current == classified:
        return False

    classified["updated_at"] = now_ts()
    trade["stop_root_cause"] = classified
    return True


def refresh_stop_root_causes(ledger):
    changed = False

    for trade in ledger.get(
        "trades",
        {},
    ).values():
        if str(
            trade.get("final_result", "")
        ).upper() != "SL":
            continue

        if update_trade_stop_root_cause(trade):
            changed = True

    return changed



def ledger_update_open_snapshot(signal):
    try:
        trade_id = ensure_ledger_trade(signal)
        ledger = load_trade_ledger()
        trade = ledger.get("trades", {}).get(trade_id)

        if trade is None:
            return

        apply_signal_tracking_to_trade(
            trade,
            signal,
        )
        save_trade_ledger(ledger)

    except Exception as exc:
        print(
            "Ledger acik takip guncelleme hatasi:",
            exc,
        )


def ledger_update_post_stop_diagnosis(
    trade_id,
    returned_level=None,
    age_minutes=None,
):
    if not trade_id:
        return

    try:
        ledger = load_trade_ledger()
        trade = ledger.get("trades", {}).get(
            str(trade_id)
        )

        if trade is None:
            return

        diagnosis = trade.setdefault(
            "diagnosis",
            build_trade_diagnosis(trade),
        )

        if returned_level:
            diagnosis["primary"] = (
                "FITIL / DAR STOP OLASILIGI"
            )
            diagnosis["confidence"] = "YUKSEK"
            diagnosis["provisional"] = False
            diagnosis.setdefault(
                "factors",
                [],
            ).append(
                f"Stop sonrasi fiyat {returned_level} seviyesine dondu."
            )

            trade["post_stop_follow"] = {
                "returned_level": returned_level,
                "age_minutes": age_minutes,
                "status": "RETURNED_TO_TARGET",
                "updated_at": now_ts(),
            }

        else:
            diagnosis["provisional"] = False
            diagnosis.setdefault(
                "factors",
                [],
            ).append(
                (
                    f"Stop sonrasi {SL_AFTER_MAX_TRACK_MINUTES} "
                    f"dakika icinde TP1'e donus olmadi."
                )
            )

            trade["post_stop_follow"] = {
                "returned_level": None,
                "age_minutes": age_minutes,
                "status": "NO_TP1_RETURN",
                "updated_at": now_ts(),
            }

        update_trade_stop_root_cause(trade)
        save_trade_ledger(ledger)

    except Exception as exc:
        print(
            "Ledger stop sonrasi teshis guncelleme hatasi:",
            exc,
        )


def ensure_ledger_trade(signal):
    ledger = load_trade_ledger()
    trades = ledger["trades"]

    trade_id = build_trade_id(signal)
    opened_at = int(
        signal.get("opened_at")
        or now_ts()
    )

    if trade_id not in trades:
        trades[trade_id] = {
            "trade_id": trade_id,
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "source": signal.get("source", "MTF"),
            "entry": safe_float(signal.get("entry")),
            "tp1": safe_float(signal.get("tp1")),
            "tp2": safe_float(signal.get("tp2")),
            "tp3": safe_float(signal.get("tp3")),
            "sl": safe_float(signal.get("sl")),
            "score": signal.get("score"),
            "risk_percent": signal.get("risk_percent"),
            "opened_at": opened_at,
            "opened_day": day_key_from_ts(opened_at),
            "tp1_hit": bool(signal.get("tp1_hit", False)),
            "tp2_hit": bool(signal.get("tp2_hit", False)),
            "tp3_hit": bool(signal.get("tp3_hit", False)),
            "status": "OPEN",
            "final_result": None,
            "r_result": None,
            "exit_price": None,
            "closed_at": None,
            "closed_day": None,
            "events": [
                {
                    "time": opened_at,
                    "event": "OPENED",
                    "price": safe_float(signal.get("entry")),
                }
            ],
        }

        apply_signal_tracking_to_trade(
            trades[trade_id],
            signal,
        )

        save_trade_ledger(ledger)

    else:
        apply_signal_tracking_to_trade(
            trades[trade_id],
            signal,
        )
        save_trade_ledger(ledger)

    return trade_id


def sync_open_signals_to_ledger():
    open_signals = load_open_signals()

    if not open_signals:
        if not os.path.exists(TRADE_LEDGER_FILE):
            save_trade_ledger(empty_trade_ledger())
        return

    changed = False
    ledger = load_trade_ledger()

    for signal in open_signals.values():
        trade_id = build_trade_id(signal)

        if trade_id in ledger["trades"]:
            continue

        opened_at = int(
            signal.get("opened_at")
            or now_ts()
        )

        ledger["trades"][trade_id] = {
            "trade_id": trade_id,
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "source": signal.get("source", "MTF"),
            "entry": safe_float(signal.get("entry")),
            "tp1": safe_float(signal.get("tp1")),
            "tp2": safe_float(signal.get("tp2")),
            "tp3": safe_float(signal.get("tp3")),
            "sl": safe_float(signal.get("sl")),
            "score": signal.get("score"),
            "risk_percent": signal.get("risk_percent"),
            "opened_at": opened_at,
            "opened_day": day_key_from_ts(opened_at),
            "tp1_hit": bool(signal.get("tp1_hit", False)),
            "tp2_hit": bool(signal.get("tp2_hit", False)),
            "tp3_hit": bool(signal.get("tp3_hit", False)),
            "status": "OPEN",
            "final_result": None,
            "r_result": None,
            "exit_price": None,
            "closed_at": None,
            "closed_day": None,
            "events": [
                {
                    "time": opened_at,
                    "event": "OPENED",
                    "price": safe_float(signal.get("entry")),
                    "migrated": True,
                }
            ],
        }

        apply_signal_tracking_to_trade(
            ledger["trades"][trade_id],
            signal,
        )

        changed = True

    if changed:
        save_trade_ledger(ledger)


def ledger_record_event(signal, result, exit_price=None):
    result = str(result).upper()
    trade_id = ensure_ledger_trade(signal)

    ledger = load_trade_ledger()
    trade = ledger["trades"].get(trade_id)

    if trade is None:
        return

    apply_signal_tracking_to_trade(
        trade,
        signal,
    )

    event_time = now_ts()

    event_exists = any(
        item.get("event") == result
        for item in trade.get("events", [])
    )

    if not event_exists:
        trade.setdefault("events", []).append({
            "time": event_time,
            "event": result,
            "price": safe_float(exit_price),
        })

    if result == "TP1":
        trade["tp1_hit"] = True

    elif result == "TP2":
        trade["tp1_hit"] = True
        trade["tp2_hit"] = True

    elif result == "TP3":
        trade["tp1_hit"] = True
        trade["tp2_hit"] = True
        trade["tp3_hit"] = True

    if result in {"TP1", "TP2"}:
        save_trade_ledger(ledger)
        return

    tp1_r = ledger_target_r(trade, "tp1")
    tp3_r = ledger_target_r(trade, "tp3")

    if result == "SL":
        trade["final_result"] = "SL"
        trade["r_result"] = -1.0

    elif result == "BE":
        if trade.get("tp2_hit"):
            trade["final_result"] = "TP2_SONRASI_BE"
        else:
            trade["final_result"] = "TP1_SONRASI_BE"

        trade["r_result"] = (
            round(0.50 * tp1_r, 4)
            if tp1_r is not None
            else None
        )

    elif result == "TP3":
        trade["final_result"] = "TP3"

        if tp1_r is not None and tp3_r is not None:
            trade["r_result"] = round(
                0.50 * tp1_r
                + 0.50 * tp3_r,
                4,
            )
        else:
            trade["r_result"] = None

    elif result == "EXPIRED":
        trade["final_result"] = "EXPIRED"
        trade["r_result"] = calculate_exit_r(
            trade,
            exit_price,
        )

    else:
        save_trade_ledger(ledger)
        return

    trade["status"] = "CLOSED"
    trade["exit_price"] = safe_float(exit_price)
    trade["closed_at"] = event_time
    trade["closed_day"] = day_key_from_ts(event_time)
    trade["duration_minutes"] = int(
        max(
            0,
            (
                event_time
                - int(trade.get("opened_at") or event_time)
            )
            / 60,
        )
    )
    trade["diagnosis"] = build_trade_diagnosis(
        trade
    )

    if result == "SL":
        update_trade_stop_root_cause(trade)

    save_trade_ledger(ledger)


def build_daily_r_report():
    """
    Tek günlük rapor:
    - Her işlemi yalnız bir kez sayar.
    - Net R performansını gösterir.
    - Stop sonrası 240 dakikalık sonucu gösterir.
    - Süre dolduktan sonraki 24 saatlik sonucu gösterir.
    - Teknik teşhislerin dağılımını özetler.

    Ana kaynak yalnızca trade_ledger.json'dır.
    """
    ledger = load_trade_ledger()

    if refresh_stop_root_causes(ledger):
        save_trade_ledger(ledger)

    trades = list(
        ledger.get("trades", {}).values()
    )
    today = today_key()

    opened_today = [
        trade
        for trade in trades
        if trade.get("opened_day") == today
    ]

    closed_today = [
        trade
        for trade in trades
        if trade.get("closed_day") == today
    ]

    measurable = [
        trade
        for trade in closed_today
        if safe_float(trade.get("r_result"))
        is not None
    ]

    open_total = sum(
        1
        for trade in trades
        if str(
            trade.get("status", "")
        ).upper() == "OPEN"
    )

    def sum_r(items):
        return round(
            sum(
                float(trade["r_result"])
                for trade in items
                if safe_float(
                    trade.get("r_result")
                ) is not None
            ),
            3,
        )

    net_r = sum_r(measurable)

    average_r = (
        round(net_r / len(measurable), 3)
        if measurable
        else 0.0
    )

    positive_count = sum(
        1
        for trade in measurable
        if float(trade["r_result"]) > 0
    )

    positive_rate = (
        round(
            positive_count
            / len(measurable)
            * 100,
            2,
        )
        if measurable
        else 0.0
    )

    result_counts = {
        "TP3": 0,
        "TP2_SONRASI_BE": 0,
        "TP1_SONRASI_BE": 0,
        "SL": 0,
        "EXPIRED": 0,
    }

    for trade in closed_today:
        final_result = str(
            trade.get("final_result") or ""
        ).upper()

        if final_result in result_counts:
            result_counts[final_result] += 1

    long_opened = sum(
        1
        for trade in opened_today
        if str(
            trade.get("direction", "")
        ).upper() == "LONG"
    )

    short_opened = sum(
        1
        for trade in opened_today
        if str(
            trade.get("direction", "")
        ).upper() == "SHORT"
    )

    radar_opened = sum(
        1
        for trade in opened_today
        if str(
            trade.get("source", "")
        ).upper() == "5M_RADAR"
    )

    normal_opened = (
        len(opened_today) - radar_opened
    )

    long_r = sum_r([
        trade
        for trade in measurable
        if str(
            trade.get("direction", "")
        ).upper() == "LONG"
    ])

    short_r = sum_r([
        trade
        for trade in measurable
        if str(
            trade.get("direction", "")
        ).upper() == "SHORT"
    ])

    ordered_closed = sorted(
        closed_today,
        key=lambda trade: int(
            trade.get("closed_at")
            or 0
        ),
    )

    current_stop_streak = 0
    max_stop_streak = 0

    for trade in ordered_closed:
        if str(
            trade.get("final_result", "")
        ).upper() == "SL":
            current_stop_streak += 1
            max_stop_streak = max(
                max_stop_streak,
                current_stop_streak,
            )
        else:
            current_stop_streak = 0

    # -----------------------------------------------------
    # STOP SONRASI 240 DAKİKALIK TEŞHİS
    # -----------------------------------------------------
    stops_today = [
        trade
        for trade in closed_today
        if str(
            trade.get("final_result", "")
        ).upper() == "SL"
    ]

    stop_return_tp1 = 0
    stop_return_tp2 = 0
    stop_return_tp3 = 0
    stop_no_return = 0
    stop_tracking = 0
    return_minutes = []

    for trade in stops_today:
        follow = (
            trade.get("post_stop_follow")
            or {}
        )
        status = str(
            follow.get("status", "")
        ).upper()
        level = str(
            follow.get("returned_level", "")
            or ""
        ).upper()

        if status == "RETURNED_TO_TARGET":
            if level == "TP3":
                stop_return_tp3 += 1
            elif level == "TP2":
                stop_return_tp2 += 1
            else:
                stop_return_tp1 += 1

            age_minutes = safe_float(
                follow.get("age_minutes")
            )

            if age_minutes is not None:
                return_minutes.append(
                    float(age_minutes)
                )

        elif status == "NO_TP1_RETURN":
            stop_no_return += 1

        else:
            stop_tracking += 1

    stop_return_total = (
        stop_return_tp1
        + stop_return_tp2
        + stop_return_tp3
    )

    stop_resolved_total = (
        stop_return_total
        + stop_no_return
    )

    fitil_rate = (
        round(
            stop_return_total
            / stop_resolved_total
            * 100,
            1,
        )
        if stop_resolved_total
        else 0.0
    )

    average_return_minute = (
        round(
            sum(return_minutes)
            / len(return_minutes),
        )
        if return_minutes
        else None
    )

    # Bugün netleşen takipler, stop dünkü olsa bile rapora girer.
    stop_follow_resolved_today = []

    for trade in trades:
        follow = (
            trade.get("post_stop_follow")
            or {}
        )
        updated_at = int(
            follow.get("updated_at")
            or 0
        )

        if (
            updated_at > 0
            and day_key_from_ts(updated_at)
            == today
        ):
            stop_follow_resolved_today.append(
                trade
            )

    stop_follow_resolved_today.sort(
        key=lambda trade: int(
            (
                trade.get("post_stop_follow")
                or {}
            ).get("updated_at")
            or 0
        )
    )

    stop_follow_lines = []

    for trade in stop_follow_resolved_today[-4:]:
        follow = (
            trade.get("post_stop_follow")
            or {}
        )
        status = str(
            follow.get("status", "")
        ).upper()
        age = int(
            safe_float(
                follow.get("age_minutes"),
                0,
            )
            or 0
        )

        if status == "RETURNED_TO_TARGET":
            level = (
                follow.get("returned_level")
                or "TP1"
            )
            result_text = (
                f"{age} dk sonra {level}"
                f" → fitil/dar stop olasılığı"
            )
        else:
            result_text = (
                f"{age} dk dönüş yok"
                f" → gerçek başarısız stop"
            )

        stop_follow_lines.append(
            f"{trade.get('symbol')} "
            f"{trade.get('direction')}"
            f" → {result_text}"
        )

    stop_follow_text = (
        "\n".join(stop_follow_lines)
        if stop_follow_lines
        else "Bugün netleşen stop sonrası takip yok."
    )

    # -----------------------------------------------------
    # SÜRE SONRASI 24 SAATLİK TEŞHİS
    # -----------------------------------------------------
    expiry_target = 0
    expiry_protection = 0
    expiry_no_decision = 0
    expiry_resolved_today = []

    for trade in trades:
        follow = (
            trade.get("post_expiry_follow")
            or {}
        )
        resolved_at = int(
            follow.get("resolved_at")
            or 0
        )

        if (
            resolved_at <= 0
            or day_key_from_ts(resolved_at)
            != today
        ):
            continue

        expiry_resolved_today.append(trade)
        outcome = str(
            follow.get("first_event", "")
        ).upper()

        if outcome == "TARGET":
            expiry_target += 1
        elif outcome == "PROTECTION":
            expiry_protection += 1
        else:
            expiry_no_decision += 1

    expiry_tracking = sum(
        1
        for trade in trades
        if str(
            trade.get(
                "post_expiry_status",
                "",
            )
        ).upper() == "TRACKING"
    )

    expiry_resolved_today.sort(
        key=lambda trade: int(
            (
                trade.get("post_expiry_follow")
                or {}
            ).get("resolved_at")
            or 0
        )
    )

    expiry_lines = []

    for trade in expiry_resolved_today[-3:]:
        follow = (
            trade.get("post_expiry_follow")
            or {}
        )
        outcome = str(
            follow.get("first_event", "")
        ).upper()
        elapsed = int(
            safe_float(
                follow.get("elapsed_minutes"),
                0,
            )
            or 0
        )
        hours = elapsed // 60
        minutes = elapsed % 60

        if outcome == "TARGET":
            label = (
                follow.get("target_label")
                or "HEDEF"
            )
            result_text = (
                f"{label} önce"
                f" → süre erken kapatmış olabilir"
            )
        elif outcome == "PROTECTION":
            label = (
                follow.get("protection_label")
                or "KORUMA"
            )
            result_text = (
                f"{label} önce"
                f" → süre sınırı korudu"
            )
        else:
            result_text = (
                "24 saatte karar yok"
            )

        expiry_lines.append(
            f"{trade.get('symbol')} "
            f"{trade.get('direction')}"
            f" → {hours}s {minutes}dk | "
            f"{result_text}"
        )

    expiry_text = (
        "\n".join(expiry_lines)
        if expiry_lines
        else "Bugün netleşen süre sonrası takip yok."
    )

    # -----------------------------------------------------
    # TEKNİK TEŞHİS DAĞILIMI
    # -----------------------------------------------------
    diagnosis_labels = {
        "KURULUM BASARILI":
            "Kurulum başarılı",
        "YON DOGRU, DEVAM GUCU ZAYIFLADI":
            "Yön doğru, devam zayıf",
        "FITIL / DAR STOP OLASILIGI":
            "Fitil/dar stop",
        "HIZLI TERS HAREKET / YON UYUMSUZLUGU":
            "Hızlı ters/yön uyumsuz",
        "ONCE LEHE GITTI, SONRA TERS DONDU":
            "Önce lehe, sonra ters",
        "KURULUM DEVAM ETMEDI":
            "Kurulum devam etmedi",
        "YON KISMEN DOGRU, HEDEF TAMAMLANMADI":
            "Yön kısmen doğru",
        "YON DEVAM ETMEDI / ZAMAN ASIMI":
            "Yön devam etmedi/zaman aşımı",
        "BELIRGIN SONUC OLUSMADI":
            "Belirgin sonuç yok",
        "KAYIT YETERSIZ":
            "Kayıt yetersiz",
    }

    diagnosis_counts = {}

    for trade in closed_today:
        diagnosis = (
            trade.get("diagnosis")
            or {}
        )
        primary = str(
            diagnosis.get(
                "primary",
                "KAYIT YETERSIZ",
            )
        ).upper()

        label = diagnosis_labels.get(
            primary,
            primary.title(),
        )

        diagnosis_counts[label] = (
            diagnosis_counts.get(label, 0)
            + 1
        )

    diagnosis_ordered = sorted(
        diagnosis_counts.items(),
        key=lambda item: (
            -item[1],
            item[0],
        ),
    )

    diagnosis_text = (
        "\n".join(
            f"• {label}: {count}"
            for label, count
            in diagnosis_ordered[:6]
        )
        if diagnosis_ordered
        else "Bugün kapanan teknik teşhis yok."
    )

    # -----------------------------------------------------
    # STOP KÖK NEDENLERİ
    # -----------------------------------------------------
    root_cause_counts = {}
    root_cause_resolved = 0

    for trade in stops_today:
        root = (
            trade.get("stop_root_cause")
            or classify_stop_root_cause(trade)
            or {}
        )
        primary = str(
            root.get("primary", "VERI_YETERSIZ")
        ).upper()
        label = (
            root.get("label")
            or STOP_ROOT_CAUSE_LABELS.get(
                primary,
                "Veri yetersiz",
            )
        )

        root_cause_counts[label] = (
            root_cause_counts.get(label, 0)
            + 1
        )

        if not root.get("provisional", True):
            root_cause_resolved += 1

    root_cause_ordered = sorted(
        root_cause_counts.items(),
        key=lambda item: (
            -item[1],
            item[0],
        ),
    )

    root_cause_count_text = (
        "\n".join(
            f"• {label}: {count}"
            for label, count
            in root_cause_ordered[:8]
        )
        if root_cause_ordered
        else "Bugün stop kök neden kaydı yok."
    )

    recent_stop_lines = []

    for trade in sorted(
        stops_today,
        key=lambda item: int(
            item.get("closed_at")
            or 0
        ),
    )[-5:]:
        root = (
            trade.get("stop_root_cause")
            or classify_stop_root_cause(trade)
            or {}
        )
        metrics = root.get("metrics") or {}
        mfe_value = safe_float(
            metrics.get("mfe_r"),
            0.0,
        )
        duration_value = int(
            safe_float(
                metrics.get("duration_minutes"),
                0,
            )
            or 0
        )
        primary_label = (
            root.get("label")
            or "Veri yetersiz"
        )

        if root.get("provisional", True):
            preliminary = root.get(
                "preliminary_label"
            )
            if preliminary:
                primary_label = (
                    f"Takip sürüyor; ilk ihtimal: "
                    f"{preliminary}"
                )

        recent_stop_lines.append(
            f"{trade.get('symbol')} "
            f"{trade.get('direction')}"
            f" → {primary_label}"
            f" | MFE {mfe_value:.2f}R"
            f" | {duration_value} dk"
        )

    recent_stop_text = (
        "\n".join(recent_stop_lines)
        if recent_stop_lines
        else "Bugün yeni stop işlemi yok."
    )

    resolved_root_items = [
        (label, count)
        for label, count in root_cause_ordered
        if label != STOP_ROOT_CAUSE_LABELS[
            "TAKIP_SURUYOR"
        ]
    ]

    dominant_root_label = None
    dominant_root_count = 0

    if resolved_root_items:
        (
            dominant_root_label,
            dominant_root_count,
        ) = resolved_root_items[0]

    # -----------------------------------------------------
    # EN İYİ / EN ZAYIF COİN: GERÇEK NET R
    # -----------------------------------------------------
    coin_net = {}

    for trade in measurable:
        symbol = str(
            trade.get("symbol") or "BILINMIYOR"
        )
        coin_net[symbol] = (
            coin_net.get(symbol, 0.0)
            + float(trade["r_result"])
        )

    if coin_net:
        best_coin, best_coin_r = max(
            coin_net.items(),
            key=lambda item: item[1],
        )
        worst_coin, worst_coin_r = min(
            coin_net.items(),
            key=lambda item: item[1],
        )
        best_worst_text = (
            f"🏆 En İyi: {best_coin} "
            f"({best_coin_r:+.3f}R)\n"
            f"⚠️ En Zayıf: {worst_coin} "
            f"({worst_coin_r:+.3f}R)"
        )
    else:
        best_worst_text = (
            "🏆 En İyi: Yok\n"
            "⚠️ En Zayıf: Yok"
        )

    # -----------------------------------------------------
    # SON NİHAİ KAPANIŞLAR
    # -----------------------------------------------------
    labels = {
        "TP3": "TP3",
        "TP2_SONRASI_BE":
            "TP2 sonrası BE",
        "TP1_SONRASI_BE":
            "TP1 sonrası BE",
        "SL": "SL",
        "EXPIRED": "Süre doldu",
    }

    recent_lines = []

    for trade in ordered_closed[-6:]:
        r_value = safe_float(
            trade.get("r_result")
        )

        r_text = (
            f"{r_value:+.3f}R"
            if r_value is not None
            else "R ölçülmedi"
        )

        recent_lines.append(
            f"{clock_from_ts(trade.get('closed_at'))}"
            f" | {trade.get('symbol')}"
            f" {trade.get('direction')}"
            f" → {labels.get(str(trade.get('final_result')).upper(), trade.get('final_result'))}"
            f" ({r_text})"
        )

    recent_text = (
        "\n".join(recent_lines)
        if recent_lines
        else "Bugün yeni nihai kapanış yok."
    )

    # -----------------------------------------------------
    # VERİYE DAYALI GÜNLÜK GÖZLEM
    # -----------------------------------------------------
    observation_lines = []

    direct_stop_rate = (
        round(
            result_counts["SL"]
            / len(measurable)
            * 100,
            1,
        )
        if measurable
        else 0.0
    )

    if len(measurable) < 10:
        observation_lines.append(
            "Örnek sayısı düşük; ayar değişikliği için "
            "tek günlük veri yeterli değil."
        )
    else:
        observation_lines.append(
            f"Doğrudan stop oranı: "
            f"%{direct_stop_rate}."
        )

    if abs(long_r - short_r) >= 2.0:
        weak_side = (
            "SHORT"
            if short_r < long_r
            else "LONG"
        )
        difference = abs(
            long_r - short_r
        )
        observation_lines.append(
            f"{weak_side} tarafı diğer yönden "
            f"{difference:.3f}R daha zayıf."
        )

    if stop_resolved_total >= 3:
        observation_lines.append(
            f"Netleşen stopların %{fitil_rate}'i "
            f"240 dakika içinde hedefe döndü."
        )

    expiry_resolved_count = (
        expiry_target
        + expiry_protection
        + expiry_no_decision
    )

    if expiry_resolved_count >= 2:
        if expiry_protection > expiry_target:
            observation_lines.append(
                "18 saat sınırı bugün daha çok "
                "kâr/zarar koruması sağladı."
            )
        elif expiry_target > expiry_protection:
            observation_lines.append(
                "Bazı işlemler 18 saatten sonra hedefe gitti; "
                "süre sınırı izlenmeli."
            )

    if (
        root_cause_resolved >= 3
        and dominant_root_label
    ):
        observation_lines.append(
            f"Bugünün en sık netleşen stop nedeni: "
            f"{dominant_root_label} "
            f"({dominant_root_count} işlem)."
        )

    observation_lines.append(
        "Filtre değişimi için aynı kök nedenin en az "
        "3 gün veya 10 işlemde tekrarı aranacak."
    )

    observation_text = "\n".join(
        f"• {line}"
        for line in observation_lines[:4]
    )

    average_return_text = (
        f"{average_return_minute} dk"
        if average_return_minute is not None
        else "Yok"
    )

    report = f"""📊 GÜNLÜK NET + KÖK NEDEN RAPORU v5

Tarih: {today}

İŞLEM ÖZETİ
Açılan: {len(opened_today)} | Kapanan: {len(closed_today)} | Açık: {open_total}
15M Giriş: {normal_opened} | 5M Radar: {radar_opened}
LONG: {long_opened} | SHORT: {short_opened}

NİHAİ SONUÇLAR
🏁 TP3: {result_counts['TP3']}
✅ TP2 sonrası BE: {result_counts['TP2_SONRASI_BE']}
✅ TP1 sonrası BE: {result_counts['TP1_SONRASI_BE']}
❌ Doğrudan SL: {result_counts['SL']}
⏳ Süresi Dolan: {result_counts['EXPIRED']}

NET PERFORMANS
Net: {net_r:+.3f}R | Ortalama: {average_r:+.3f}R
Pozitif Kapanış: %{positive_rate}
En Uzun Stop Serisi: {max_stop_streak}
🟢 LONG: {long_r:+.3f}R | 🔴 SHORT: {short_r:+.3f}R

STOP SONRASI 240 DK
TP1'e Dönen: {stop_return_tp1}
TP2'ye Dönen: {stop_return_tp2}
TP3'e Dönen: {stop_return_tp3}
Dönmeyen: {stop_no_return}
Takibi Süren: {stop_tracking}
Fitil/Dar Stop Oranı: %{fitil_rate}
Ortalama Dönüş Süresi: {average_return_text}

Bugün Netleşen SL Takipleri:
{stop_follow_text}

SÜRE SONRASI 24 SAAT
Hedef Önce: {expiry_target}
Koruma Önce: {expiry_protection}
Kararsız: {expiry_no_decision}
Hâlâ Takipte: {expiry_tracking}

Bugün Netleşen Süre Takipleri:
{expiry_text}

TEKNİK TEŞHİSLER
{diagnosis_text}

STOP KÖK NEDENLERİ
{root_cause_count_text}

Son Stop Teşhisleri:
{recent_stop_text}

{best_worst_text}

SON NİHAİ KAPANIŞLAR
{recent_text}

GÜNLÜK GÖZLEM
{observation_text}

Not: Her işlem yalnız bir kez sayılır. Net R, stop sonrası ve süre sonrası teşhisler trade_ledger.json verisinden alınır."""

    # Telegram mesaj sınırının altında kal.
    if len(report) > 4050:
        report = report[:4000] + (
            "\n\nRapor mesaj sınırı nedeniyle kısaltıldı."
        )

    return report


# =========================================================
# ESKI PERFORMANCE RAPORU
# =========================================================

def ensure_perf_day(performance):
    today = today_key()

    performance.setdefault("days", {})

    performance["days"].setdefault(today, {
        "opened": 0,
        "radar": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "sl": 0,
        "be": 0,
        "expired": 0,
        "long": 0,
        "short": 0,
        "normal": 0,
        "radar_trade": 0,
        "coins": {},
        "direction_stops": {},
        "stop_times": {},
        "closed_times": {},
        "closed_results": {},
        "closed_history": [],
        "sl_after_tp1": 0,
        "sl_after_tp2": 0,
        "sl_after_no_return": 0,
    })

    day = performance["days"][today]

    day.setdefault("coins", {})
    day.setdefault("direction_stops", {})
    day.setdefault("stop_times", {})
    day.setdefault("closed_times", {})
    day.setdefault("closed_results", {})
    day.setdefault("closed_history", [])
    day.setdefault("sl_after_tp1", 0)
    day.setdefault("sl_after_tp2", 0)
    day.setdefault("sl_after_no_return", 0)

    performance.setdefault("sl_after_follow", {})

    return performance


def add_history(day, item):
    day.setdefault("closed_history", [])
    day["closed_history"].append(item)

    if len(day["closed_history"]) > 100:
        day["closed_history"] = day["closed_history"][-100:]


def update_performance(
    symbol,
    result,
    direction=None,
    source=None,
    entry=None,
    exit_price=None,
    score=None,
):
    performance = ensure_perf_day(
        load_performance()
    )

    today = today_key()
    day = performance["days"][today]

    if result == "OPENED":
        day["opened"] += 1

        if direction == "LONG":
            day["long"] += 1
        elif direction == "SHORT":
            day["short"] += 1

        if source == "5M_RADAR":
            day["radar_trade"] += 1
        else:
            day["normal"] += 1

    elif result == "RADAR":
        day["radar"] += 1

    elif result in {
        "TP1",
        "TP2",
        "TP3",
        "SL",
        "BE",
        "EXPIRED",
    }:
        key = result.lower()
        day[key] = int(day.get(key, 0)) + 1

        if result == "SL" and direction in {"LONG", "SHORT"}:
            day["direction_stops"][direction] = (
                int(
                    day["direction_stops"].get(
                        direction,
                        0,
                    )
                )
                + 1
            )
            day["stop_times"][symbol] = now_ts()

        if result in {"TP3", "BE", "EXPIRED"}:
            day["closed_times"][symbol] = now_ts()
            day["closed_results"][symbol] = result

        add_history(day, {
            "time": datetime.now(
                TR_TIMEZONE
            ).strftime("%H:%M:%S"),
            "symbol": symbol,
            "direction": direction,
            "result": result,
            "entry": entry,
            "exit": exit_price,
            "source": source,
            "score": score,
        })

    day["coins"].setdefault(symbol, {
        "opened": 0,
        "radar": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "sl": 0,
        "be": 0,
        "expired": 0,
    })

    coin = day["coins"][symbol]

    if result == "OPENED":
        coin["opened"] += 1

    elif result == "RADAR":
        coin["radar"] += 1

    elif result in {
        "TP1",
        "TP2",
        "TP3",
        "SL",
        "BE",
        "EXPIRED",
    }:
        coin[result.lower()] = (
            int(
                coin.get(
                    result.lower(),
                    0,
                )
            )
            + 1
        )

    performance["last_update"] = now_ts()
    save_performance(performance)


def get_today_sl_count():
    day = (
        load_performance()
        .get("days", {})
        .get(today_key(), {})
    )

    return int(day.get("sl", 0))


def risk_mode_active():
    return (
        get_today_sl_count()
        >= RISK_MODE_STOP_COUNT
    )


def has_recent_stop(symbol):
    day = (
        load_performance()
        .get("days", {})
        .get(today_key(), {})
    )

    stop_time = int(
        day.get(
            "stop_times",
            {},
        ).get(symbol, 0)
    )

    if stop_time <= 0:
        return False

    return (
        now_ts() - stop_time
        < STOPPED_COIN_COOLDOWN_HOURS
        * 60
        * 60
    )


def has_recent_closed_signal(symbol):
    day = (
        load_performance()
        .get("days", {})
        .get(today_key(), {})
    )

    closed_time = int(
        day.get(
            "closed_times",
            {},
        ).get(symbol, 0)
    )

    if closed_time <= 0:
        return False

    return (
        now_ts() - closed_time
        < RECENT_CLOSED_COIN_COOLDOWN_SECONDS
    )


# =========================================================
# OKX VERI
# =========================================================

def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap",
        },
    })


def to_okx_symbol(symbol):
    base = str(symbol).replace("USDT", "")
    return f"{base}/USDT:USDT"


def okx_symbol_to_bot_symbol(okx_symbol):
    base = str(okx_symbol).split("/")[0]
    return f"{base}USDT".upper()


def safe_quote_volume(ticker):
    try:
        value = ticker.get("quoteVolume")

        if value is not None:
            return float(value)

        info = ticker.get("info", {})

        for key in (
            "volCcy24h",
            "volUsd24h",
            "vol24h",
        ):
            value = info.get(key)

            if value is not None:
                return float(value)

    except Exception:
        pass

    return 0.0


def get_scan_coins(exchange):
    if not AUTO_TOP_VOLUME_SCAN:
        return PRIORITY_COINS

    try:
        markets = exchange.load_markets()
        okx_symbols = []

        stable_bases = {
            "USDT",
            "USDC",
            "DAI",
            "FDUSD",
            "TUSD",
            "USDP",
            "USD",
        }

        for market in markets.values():
            if not market.get("active", True):
                continue

            if not market.get("swap", False):
                continue

            if market.get("quote") != "USDT":
                continue

            if market.get("settle") != "USDT":
                continue

            okx_symbol = market.get("symbol")

            if (
                not okx_symbol
                or "/USDT:USDT" not in okx_symbol
            ):
                continue

            base = str(
                market.get("base", "")
            ).upper()

            if not base or base in stable_bases:
                continue

            okx_symbols.append(okx_symbol)

        tickers = exchange.fetch_tickers(
            okx_symbols
        )

        rows = []

        for okx_symbol in okx_symbols:
            ticker = tickers.get(
                okx_symbol,
                {},
            )

            volume = safe_quote_volume(
                ticker
            )

            if volume < MIN_24H_QUOTE_VOLUME:
                continue

            rows.append((
                okx_symbol_to_bot_symbol(
                    okx_symbol
                ),
                volume,
            ))

        if not rows:
            return PRIORITY_COINS

        rows.sort(
            key=lambda item: item[1],
            reverse=True,
        )

        volume_coins = [
            coin
            for coin, _ in rows
        ]

        priority = [
            coin
            for coin in PRIORITY_COINS
            if coin in volume_coins
        ]

        others = [
            coin
            for coin in volume_coins
            if coin not in priority
        ]

        scan_coins = (
            priority + others
        )[:MAX_SCAN_COINS]

        print("Hacimli coin sayısı:", len(rows))
        print("Taranacak coin:", len(scan_coins))
        print("İlk 10:", scan_coins[:10])

        return scan_coins

    except Exception as exc:
        print(
            "Top volume tarama hatası:",
            exc,
        )
        return PRIORITY_COINS


def fetch_df(
    exchange,
    symbol,
    timeframe,
    limit,
    min_len=30,
):
    try:
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            limit=limit,
        )

        if not ohlcv or len(ohlcv) < min_len:
            return None

        return pd.DataFrame(
            ohlcv,
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

    except Exception as exc:
        print(
            symbol,
            timeframe,
            "veri hatası:",
            exc,
        )
        return None


def simple_ema(series, span):
    return series.ewm(
        span=span,
        adjust=False,
    ).mean()


def get_market_direction_status(exchange):
    if not MARKET_GUARD_ENABLED:
        return {
            "LONG": True,
            "SHORT": True,
            "reason": "Market koruma kapalı",
        }

    long_ok = 0
    short_ok = 0
    hard_red = 0
    hard_green = 0
    details = []

    for ref_symbol in MARKET_REFERENCE_COINS:
        try:
            df15 = fetch_df(
                exchange,
                ref_symbol,
                ENTRY_TIMEFRAME,
                80,
                min_len=40,
            )

            df5 = fetch_df(
                exchange,
                ref_symbol,
                RADAR_TIMEFRAME,
                40,
                min_len=20,
            )

            if df15 is None or df5 is None:
                continue

            df15 = df15.copy()
            df15["ema20"] = simple_ema(
                df15["close"],
                20,
            )

            last15 = df15.iloc[-2]
            close15 = float(last15["close"])
            ema20 = float(last15["ema20"])

            last5 = df5.iloc[-2]

            move5 = (
                (
                    float(last5["close"])
                    - float(last5["open"])
                )
                / float(last5["open"])
                * 100
            )

            ref_long_ok = (
                close15 >= ema20
                and move5
                > -MARKET_MAX_COUNTER_5M_MOVE_PERCENT
            )

            ref_short_ok = (
                close15 <= ema20
                and move5
                < MARKET_MAX_COUNTER_5M_MOVE_PERCENT
            )

            if ref_long_ok:
                long_ok += 1

            if ref_short_ok:
                short_ok += 1

            if (
                move5
                <= -MARKET_MAX_COUNTER_5M_MOVE_PERCENT
            ):
                hard_red += 1

            if (
                move5
                >= MARKET_MAX_COUNTER_5M_MOVE_PERCENT
            ):
                hard_green += 1

            details.append(
                f"{ref_symbol}: 15M "
                f"{'EMA20 üstü' if close15 >= ema20 else 'EMA20 altı'}, "
                f"5M %{round(move5, 2)}"
            )

        except Exception as exc:
            print(
                ref_symbol,
                "market koruma veri hatası:",
                exc,
            )

    allow_long = (
        long_ok >= MARKET_LONG_MIN_OK_COUNT
        and hard_red < 2
    )

    allow_short = (
        short_ok >= MARKET_SHORT_MIN_OK_COUNT
        and hard_green < 2
    )

    reason = (
        f"LONG uygun: {long_ok}/"
        f"{len(MARKET_REFERENCE_COINS)} | "
        f"SHORT uygun: {short_ok}/"
        f"{len(MARKET_REFERENCE_COINS)} | "
        f"Sert kırmızı: {hard_red} | "
        f"Sert yeşil: {hard_green} | "
        + " | ".join(details)
    )

    print("Market koruma:", reason)

    return {
        "LONG": allow_long,
        "SHORT": allow_short,
        "reason": reason,
    }


def fetch_candles_since(
    exchange,
    symbol,
    timeframe,
    since_seconds,
    limit=180,
):
    try:
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            since=max(
                0,
                int(since_seconds),
            ) * 1000,
            limit=limit,
        )

        return [
            {
                "time": int(item[0] / 1000),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
            }
            for item in ohlcv
        ]

    except Exception as exc:
        print(
            symbol,
            "takip mum hatası:",
            exc,
        )
        return []


def get_current_price(exchange, symbol):
    try:
        ticker = exchange.fetch_ticker(
            to_okx_symbol(symbol)
        )

        price = ticker.get("last")

        return (
            float(price)
            if price is not None
            else None
        )

    except Exception as exc:
        print(
            symbol,
            "güncel fiyat hatası:",
            exc,
        )
        return None


# =========================================================
# GIRIS / DUPLICATE / LIMIT
# =========================================================

def is_entry_still_valid(signal, current_price):
    try:
        entry = float(signal["entry"])
        tp1 = float(signal["tp1"])
        sl = float(signal["sl"])
        direction = signal["direction"]

        if current_price is None or entry <= 0:
            return False, "güncel fiyat yok"

        entry_distance = abs(
            (current_price - entry)
            / entry
        ) * 100

        if (
            entry_distance
            > MAX_ENTRY_DISTANCE_PERCENT
        ):
            return (
                False,
                f"girişten uzak: "
                f"%{round(entry_distance, 2)}",
            )

        if direction == "LONG":
            total = tp1 - entry
            progressed = current_price - entry

            if total <= 0:
                return False, "TP1 hatalı"

            progress_percent = (
                progressed / total * 100
            )

            if (
                progress_percent
                >= MAX_TP1_PROGRESS_PERCENT
            ):
                return (
                    False,
                    f"TP1'e yaklaşmış: "
                    f"%{round(progress_percent, 2)}",
                )

            if current_price >= tp1:
                return False, "TP1 zaten gelmiş"

            if current_price <= sl:
                return False, "SL tarafında"

        else:
            total = entry - tp1
            progressed = entry - current_price

            if total <= 0:
                return False, "TP1 hatalı"

            progress_percent = (
                progressed / total * 100
            )

            if (
                progress_percent
                >= MAX_TP1_PROGRESS_PERCENT
            ):
                return (
                    False,
                    f"TP1'e yaklaşmış: "
                    f"%{round(progress_percent, 2)}",
                )

            if current_price <= tp1:
                return False, "TP1 zaten gelmiş"

            if current_price >= sl:
                return False, "SL tarafında"

        return True, "uygun"

    except Exception as exc:
        return (
            False,
            f"giriş kontrol hatası: {exc}",
        )


def is_duplicate(signal, radar=False):
    last_signals = load_last_signals()
    prefix = "RADAR" if radar else "TRADE"

    key = (
        f"{prefix}_"
        f"{signal['symbol']}_"
        f"{signal['direction']}"
    )

    last_time = int(
        last_signals.get(key, 0)
    )

    wait = (
        RADAR_DUPLICATE_BLOCK_SECONDS
        if radar
        else TRADE_DUPLICATE_BLOCK_SECONDS
    )

    return now_ts() - last_time < wait


def mark_sent(signal, radar=False):
    last_signals = load_last_signals()
    prefix = "RADAR" if radar else "TRADE"

    key = (
        f"{prefix}_"
        f"{signal['symbol']}_"
        f"{signal['direction']}"
    )

    last_signals[key] = now_ts()
    save_last_signals(last_signals)


def has_open_same_symbol(symbol):
    return any(
        signal.get("symbol") == symbol
        for signal in load_open_signals().values()
    )


def count_open_signal_risk():
    open_signals = load_open_signals()

    risky = 0
    reduced = 0

    for signal in open_signals.values():
        if bool(signal.get("tp1_hit", False)):
            reduced += 1
        else:
            risky += 1

    return risky, reduced, len(open_signals)


def build_limit_watch_message(
    signal,
    current_price=None,
):
    try:
        icon = (
            "🟢"
            if signal.get("direction") == "LONG"
            else "🔴"
        )

        price_line = ""

        if current_price is not None:
            price_line = (
                f"\n💰 Güncel Fiyat: "
                f"{format_price(current_price)}"
            )

        return (
            f"⚠️ AÇIK SİNYAL SINIRI DOLU - TAKİP\n\n"
            f"{icon} {signal.get('direction')}\n"
            f"🟡 Coin: {signal.get('symbol')}\n"
            f"⏱️ Kaynak: {signal.get('source')}\n\n"
            f"📌 Giriş: "
            f"{format_price(float(signal.get('entry')))}\n"
            f"🎯 TP1: "
            f"{format_price(float(signal.get('tp1')))}\n"
            f"🎯 TP2: "
            f"{format_price(float(signal.get('tp2')))}\n"
            f"🎯 TP3: "
            f"{format_price(float(signal.get('tp3')))}\n"
            f"🛑 SL: "
            f"{format_price(float(signal.get('sl')))}\n\n"
            f"📊 Kalite Uyum Skoru: {signal.get('score')}/100\n"
            f"🛡️ Stop Mesafesi: "
            f"%{signal.get('risk_percent')}"
            f"{price_line}\n\n"
            f"📌 Not: TP1 görmemiş açık işlem sınırı dolu olduğu için "
            f"bu sinyal işlem olarak kaydedilmedi.\n"
            f"Grafikte sadece takip et. Yeni işlem açma konusunda acele etme."
        )

    except Exception:
        return (
            f"⚠️ AÇIK SİNYAL SINIRI DOLU - TAKİP\n\n"
            f"Coin: {signal.get('symbol')}\n"
            f"Yön: {signal.get('direction')}\n"
            f"Skor: {signal.get('score')}\n\n"
            f"Bu sinyal işlem olarak kaydedilmedi."
        )


# =========================================================
# STOP SONRASI TAKIP
# =========================================================

def add_sl_after_follow(signal, exit_price):
    try:
        performance = ensure_perf_day(
            load_performance()
        )

        follow = performance.setdefault(
            "sl_after_follow",
            {},
        )

        stopped_at = now_ts()

        key = (
            f"{signal.get('symbol')}_"
            f"{signal.get('direction')}_"
            f"{stopped_at}"
        )

        follow[key] = {
            "trade_id": signal.get("trade_id"),
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "source": signal.get("source"),
            "entry": signal.get("entry"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "tp3": signal.get("tp3"),
            "sl": signal.get("sl"),
            "score": signal.get("score"),
            "risk_percent": signal.get("risk_percent"),
            "stopped_at": stopped_at,
            "stop_exit": exit_price,
            "reported_checkpoints": [],
            "after_sl_tp1": False,
            "after_sl_tp2": False,
            "after_sl_tp3": False,
            "resolved": False,
        }

        save_performance(performance)

    except Exception as exc:
        print(
            "SL sonrası takip ekleme hatası:",
            exc,
        )


def close_signal_result(
    symbol,
    signal,
    result,
    exit_price,
):
    update_performance(
        symbol=symbol,
        result=result,
        direction=signal.get("direction"),
        source=signal.get("source"),
        entry=signal.get("entry"),
        exit_price=exit_price,
        score=signal.get("score"),
    )

    ledger_record_event(
        signal,
        result,
        exit_price,
    )

    if result == "EXPIRED":
        initialize_post_expiry_follow(
            signal,
            exit_price,
        )

    if result == "SL":
        add_sl_after_follow(
            signal,
            exit_price,
        )


def register_partial_result(
    symbol,
    signal,
    result,
    exit_price,
):
    update_performance(
        symbol=symbol,
        result=result,
        direction=signal.get("direction"),
        source=signal.get("source"),
        entry=signal.get("entry"),
        exit_price=exit_price,
        score=signal.get("score"),
    )

    ledger_record_event(
        signal,
        result,
        exit_price,
    )




# =========================================================
# SÜRE DOLDUKTAN SONRA SESSİZ TAKİP
# =========================================================

def calculate_directional_r(trade, price):
    """
    Fiyatın orijinal giriş ve stop mesafesine göre yönsel R değerini
    hesaplar. TP1 sonrası kısmi kâr hesabını içermez; yalnızca fiyatın
    sinyal yönündeki gerçek hareketini ölçer.
    """
    entry = safe_float(trade.get("entry"))
    sl = safe_float(trade.get("sl"))
    value = safe_float(price)

    if entry is None or sl is None or value is None:
        return None

    risk = abs(entry - sl)

    if risk <= 0:
        return None

    direction = str(
        trade.get("direction", "")
    ).upper()

    if direction == "LONG":
        return round((value - entry) / risk, 4)

    if direction == "SHORT":
        return round((entry - value) / risk, 4)

    return None


def build_post_expiry_payload(
    trade,
    restored=False,
):
    """
    Süre dolduktan sonra hangi seviyenin önce görüleceğini izlemek için
    gerekli takip kaydını hazırlar.
    """
    entry = safe_float(trade.get("entry"))
    sl = safe_float(trade.get("sl"))
    tp1 = safe_float(trade.get("tp1"))
    tp3 = safe_float(trade.get("tp3"))

    if entry is None or sl is None:
        return None

    # TP1 daha önce görülmüşse kalan pozisyon açısından anlamlı hedef
    # TP3, koruma seviyesi ise giriş (BE) kabul edilir.
    if bool(trade.get("tp1_hit", False)):
        target_price = tp3
        target_label = "TP3"
        protection_price = entry
        protection_label = "BE"
    else:
        target_price = tp1
        target_label = "TP1"
        protection_price = sl
        protection_label = "SL"

    if target_price is None or protection_price is None:
        return None

    expired_at = int(
        trade.get("closed_at")
        or now_ts()
    )
    expiry_price = safe_float(
        trade.get("exit_price")
    )

    initial_r = calculate_directional_r(
        trade,
        expiry_price,
    )

    return {
        "version": "POST_EXPIRY_V1",
        "status": "TRACKING",
        "restored": bool(restored),
        "expired_at": expired_at,
        "expiry_price": expiry_price,
        "expiry_net_r": safe_float(
            trade.get("r_result")
        ),
        "target_label": target_label,
        "target_price": target_price,
        "protection_label": protection_label,
        "protection_price": protection_price,
        "timeframe": POST_EXPIRY_TIMEFRAME,
        "max_track_hours": POST_EXPIRY_MAX_TRACK_HOURS,
        "checkpoints": [],
        "last_checked_at": expired_at,
        "best_directional_r": initial_r,
        "worst_directional_r": initial_r,
        "best_price": expiry_price,
        "worst_price": expiry_price,
        "first_event": None,
        "first_event_at": None,
        "resolved_at": None,
        "elapsed_minutes": None,
        "diagnosis": None,
        "telegram_notified": False,
    }


def initialize_post_expiry_follow(
    signal,
    expiry_price,
):
    """
    Yeni EXPIRED işlem kapanınca artçı takip kaydını oluşturur.
    """
    trade_id = build_trade_id(signal)
    ledger = load_trade_ledger()
    trade = ledger.get(
        "trades",
        {},
    ).get(trade_id)

    if not trade:
        return

    if str(
        trade.get("final_result", "")
    ).upper() != "EXPIRED":
        return

    if trade.get("post_expiry_follow"):
        return

    if trade.get("exit_price") is None:
        trade["exit_price"] = safe_float(
            expiry_price
        )

    payload = build_post_expiry_payload(
        trade,
        restored=False,
    )

    if payload is None:
        return

    trade["post_expiry_follow"] = payload
    trade["post_expiry_status"] = "TRACKING"
    trade["post_expiry_tp1"] = False
    trade["post_expiry_sl"] = False
    trade["post_expiry_best_r"] = payload.get(
        "best_directional_r"
    )
    trade["post_expiry_worst_r"] = payload.get(
        "worst_directional_r"
    )
    trade["expiry_diagnosis"] = None

    save_trade_ledger(ledger)


def restore_recent_expired_follow_records(
    ledger,
):
    """
    Yeni özellik yüklenmeden önce EXPIRED olmuş yakın işlemleri de
    artçı takibe alır. Eski ve çok uzak kayıtlar Telegram kalabalığı
    oluşturmaması için yalnız son 48 saat içinde geri yüklenir.
    """
    restored = 0
    current_time = now_ts()

    for trade in ledger.get(
        "trades",
        {},
    ).values():
        try:
            if str(
                trade.get("final_result", "")
            ).upper() != "EXPIRED":
                continue

            if trade.get("post_expiry_follow"):
                continue

            expired_at = int(
                trade.get("closed_at")
                or 0
            )

            if expired_at <= 0:
                continue

            age_hours = (
                current_time - expired_at
            ) / 3600

            if (
                age_hours < 0
                or age_hours
                > POST_EXPIRY_RESTORE_MAX_HOURS
            ):
                continue

            payload = build_post_expiry_payload(
                trade,
                restored=True,
            )

            if payload is None:
                continue

            trade["post_expiry_follow"] = payload
            trade["post_expiry_status"] = "TRACKING"
            trade["post_expiry_tp1"] = False
            trade["post_expiry_sl"] = False
            trade["post_expiry_best_r"] = payload.get(
                "best_directional_r"
            )
            trade["post_expiry_worst_r"] = payload.get(
                "worst_directional_r"
            )
            trade["expiry_diagnosis"] = None
            restored += 1

        except Exception as exc:
            print(
                trade.get("trade_id"),
                "süre sonrası geri yükleme hatası:",
                exc,
            )

    return restored


def post_expiry_level_hits(
    direction,
    high,
    low,
    target_price,
    protection_price,
):
    direction = str(direction).upper()

    if direction == "LONG":
        return (
            high >= target_price,
            low <= protection_price,
        )

    if direction == "SHORT":
        return (
            low <= target_price,
            high >= protection_price,
        )

    return False, False


def resolve_post_expiry_ambiguous_candle(
    exchange,
    trade,
    candle_time,
    target_price,
    protection_price,
    minimum_time,
):
    """
    5M mumda hedef ve koruma aynı anda görünürse 1M alt mumlara iner.
    Aynı 1M mumda da ikisi görülürse sıra kesin belirlenemediği için
    AMBIGUOUS döndürür.
    """
    symbol = trade.get("symbol")
    direction = trade.get("direction")

    candles = fetch_candles_since(
        exchange,
        symbol,
        "1m",
        since_seconds=max(
            int(minimum_time),
            int(candle_time),
        ),
        limit=8,
    )

    candle_end = int(candle_time) + 5 * 60

    for candle in candles:
        minute_time = int(
            candle.get("time", 0)
            or 0
        )

        if minute_time < int(minimum_time):
            continue

        if minute_time >= candle_end:
            break

        target_hit, protection_hit = (
            post_expiry_level_hits(
                direction,
                float(candle["high"]),
                float(candle["low"]),
                target_price,
                protection_price,
            )
        )

        if target_hit and protection_hit:
            return "AMBIGUOUS", minute_time

        if target_hit:
            return "TARGET", minute_time

        if protection_hit:
            return "PROTECTION", minute_time

    return None, None


def update_post_expiry_excursion(
    trade,
    follow,
    high,
    low,
):
    direction = str(
        trade.get("direction", "")
    ).upper()

    if direction == "LONG":
        favorable_price = high
        adverse_price = low
    elif direction == "SHORT":
        favorable_price = low
        adverse_price = high
    else:
        return

    favorable_r = calculate_directional_r(
        trade,
        favorable_price,
    )
    adverse_r = calculate_directional_r(
        trade,
        adverse_price,
    )

    current_best = safe_float(
        follow.get("best_directional_r")
    )
    current_worst = safe_float(
        follow.get("worst_directional_r")
    )

    if (
        favorable_r is not None
        and (
            current_best is None
            or favorable_r > current_best
        )
    ):
        follow["best_directional_r"] = favorable_r
        follow["best_price"] = favorable_price

    if (
        adverse_r is not None
        and (
            current_worst is None
            or adverse_r < current_worst
        )
    ):
        follow["worst_directional_r"] = adverse_r
        follow["worst_price"] = adverse_price


def post_expiry_diagnosis_text(
    outcome,
):
    if outcome == "TARGET":
        return (
            "Süre sınırı işlemi erken kapatmış olabilir; "
            "hedef süre dolduktan sonra görüldü."
        )

    if outcome == "PROTECTION":
        return (
            "Süre sınırı daha büyük kayıptan korudu; "
            "koruma seviyesi süre dolduktan sonra önce görüldü."
        )

    if outcome == "AMBIGUOUS":
        return (
            "Hedef ve koruma aynı 1M mumda görüldü; "
            "hangisinin önce olduğu kesin belirlenemedi."
        )

    return (
        "24 saat içinde hedef veya koruma seviyesi oluşmadı; "
        "hareket yatay veya kararsız kaldı."
    )


def finalize_post_expiry_follow(
    trade,
    follow,
    outcome,
    event_time,
    event_price,
):
    expired_at = int(
        follow.get("expired_at")
        or trade.get("closed_at")
        or event_time
    )

    elapsed_minutes = int(
        max(
            0,
            (int(event_time) - expired_at) / 60,
        )
    )

    diagnosis = post_expiry_diagnosis_text(
        outcome
    )

    follow["status"] = "RESOLVED"
    follow["first_event"] = outcome
    follow["first_event_at"] = int(event_time)
    follow["first_event_price"] = safe_float(
        event_price
    )
    follow["resolved_at"] = now_ts()
    follow["elapsed_minutes"] = elapsed_minutes
    follow["diagnosis"] = diagnosis

    target_label = str(
        follow.get("target_label", "HEDEF")
    )
    protection_label = str(
        follow.get(
            "protection_label",
            "KORUMA",
        )
    )

    if outcome == "TARGET":
        status_text = (
            f"{target_label}_AFTER_EXPIRY"
        )
    elif outcome == "PROTECTION":
        status_text = (
            f"{protection_label}_AFTER_EXPIRY"
        )
    elif outcome == "AMBIGUOUS":
        status_text = "AMBIGUOUS_AFTER_EXPIRY"
    else:
        status_text = "NO_DECISION_24H"

    trade["post_expiry_status"] = status_text
    trade["post_expiry_tp1"] = bool(
        outcome == "TARGET"
        and target_label == "TP1"
    )
    trade["post_expiry_sl"] = bool(
        outcome == "PROTECTION"
        and protection_label == "SL"
    )
    trade["post_expiry_target"] = bool(
        outcome == "TARGET"
    )
    trade["post_expiry_protection"] = bool(
        outcome == "PROTECTION"
    )
    trade["post_expiry_best_r"] = follow.get(
        "best_directional_r"
    )
    trade["post_expiry_worst_r"] = follow.get(
        "worst_directional_r"
    )
    trade["expiry_diagnosis"] = diagnosis

    event_name = (
        "POST_EXPIRY_"
        + (
            outcome
            if outcome
            else "NO_DECISION"
        )
    )

    event_exists = any(
        item.get("event") == event_name
        for item in trade.get("events", [])
    )

    if not event_exists:
        trade.setdefault("events", []).append({
            "time": int(event_time),
            "event": event_name,
            "price": safe_float(event_price),
        })


def build_post_expiry_telegram(
    trade,
    follow,
):
    outcome = str(
        follow.get("first_event")
        or "NO_DECISION"
    )
    symbol = trade.get("symbol")
    direction = trade.get("direction")
    expiry_r = safe_float(
        follow.get("expiry_net_r")
    )
    expiry_r_text = (
        f"{expiry_r:+.3f}R"
        if expiry_r is not None
        else "ölçülemedi"
    )
    elapsed_minutes = int(
        follow.get("elapsed_minutes")
        or 0
    )
    elapsed_hours = elapsed_minutes // 60
    remaining_minutes = elapsed_minutes % 60
    elapsed_text = (
        f"{elapsed_hours} saat "
        f"{remaining_minutes} dakika"
    )

    target_label = str(
        follow.get("target_label", "HEDEF")
    )
    protection_label = str(
        follow.get(
            "protection_label",
            "KORUMA",
        )
    )

    if outcome == "TARGET":
        result_text = (
            f"{target_label} seviyesi önce görüldü."
        )
    elif outcome == "PROTECTION":
        result_text = (
            f"{protection_label} seviyesi önce görüldü."
        )
    elif outcome == "AMBIGUOUS":
        result_text = (
            f"{target_label} ve {protection_label} "
            f"aynı 1M mumda görüldü."
        )
    else:
        result_text = (
            f"{POST_EXPIRY_MAX_TRACK_HOURS} saat içinde "
            f"{target_label}/{protection_label} oluşmadı."
        )

    return (
        f"🔎 SÜRE SONRASI NİHAİ TAKİP\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"{MAX_OPEN_SIGNAL_HOURS} saat sonu: "
        f"{expiry_r_text}\n"
        f"Süre sonrası takip: {elapsed_text}\n\n"
        f"Sonuç: {result_text}\n"
        f"Teşhis: {follow.get('diagnosis')}"
    )


def check_post_expiry_follow(exchange):
    """
    EXPIRED işlemleri 24 saat boyunca arka planda izler.
    6 ve 12 saat kontrolleri yalnız JSON'a yazılır.
    Telegram mesajı ancak hedef/koruma sonucu netleştiğinde veya
    24 saat sonunda gönderilir.
    """
    ledger = load_trade_ledger()
    restored_count = (
        restore_recent_expired_follow_records(
            ledger
        )
    )
    changed = restored_count > 0

    if restored_count:
        print(
            "Süre sonrası takibe geri alınan işlem:",
            restored_count,
        )

    current_time = now_ts()

    for trade_id, trade in ledger.get(
        "trades",
        {},
    ).items():
        follow = trade.get(
            "post_expiry_follow"
        )

        if not isinstance(follow, dict):
            continue

        if str(
            follow.get("status", "")
        ).upper() != "TRACKING":
            continue

        try:
            symbol = trade.get("symbol")
            direction = trade.get("direction")
            expired_at = int(
                follow.get("expired_at")
                or trade.get("closed_at")
                or 0
            )
            target_price = safe_float(
                follow.get("target_price")
            )
            protection_price = safe_float(
                follow.get("protection_price")
            )

            if (
                expired_at <= 0
                or target_price is None
                or protection_price is None
            ):
                continue

            max_end_time = (
                expired_at
                + POST_EXPIRY_MAX_TRACK_HOURS
                * 3600
            )

            candles = fetch_candles_since(
                exchange,
                symbol,
                POST_EXPIRY_TIMEFRAME,
                since_seconds=max(
                    0,
                    expired_at - 5 * 60,
                ),
                limit=POST_EXPIRY_TRACK_LIMIT,
            )

            if not candles:
                print(
                    symbol,
                    "süre sonrası mum verisi alınamadı.",
                )
                continue

            outcome = None
            outcome_time = None
            outcome_price = None
            last_price = safe_float(
                follow.get("expiry_price")
            )

            for candle in candles:
                candle_time = int(
                    candle.get("time", 0)
                    or 0
                )
                candle_end = candle_time + 5 * 60

                if candle_end <= expired_at:
                    continue

                if candle_time > max_end_time:
                    break

                high = float(candle["high"])
                low = float(candle["low"])
                close = float(candle["close"])
                last_price = close

                update_post_expiry_excursion(
                    trade,
                    follow,
                    high,
                    low,
                )

                target_hit, protection_hit = (
                    post_expiry_level_hits(
                        direction,
                        high,
                        low,
                        target_price,
                        protection_price,
                    )
                )

                # Süre sonunun içinde kaldığı ilk kısmi 5M mum veya
                # iki seviyenin aynı 5M mumda görülmesi için 1M çözüm.
                needs_minute_resolution = (
                    candle_time < expired_at
                    or (
                        target_hit
                        and protection_hit
                    )
                )

                if needs_minute_resolution and (
                    target_hit
                    or protection_hit
                ):
                    minute_outcome, minute_time = (
                        resolve_post_expiry_ambiguous_candle(
                            exchange,
                            trade,
                            candle_time,
                            target_price,
                            protection_price,
                            expired_at,
                        )
                    )

                    if minute_outcome:
                        outcome = minute_outcome
                        outcome_time = minute_time
                        outcome_price = (
                            target_price
                            if outcome == "TARGET"
                            else (
                                protection_price
                                if outcome == "PROTECTION"
                                else close
                            )
                        )
                        break

                    # Kısmi mumdaki hareketin süre dolmadan gerçekleşmiş
                    # olma ihtimali varsa o mumu sonuç olarak kullanma.
                    if candle_time < expired_at:
                        continue

                if target_hit:
                    outcome = "TARGET"
                    outcome_time = candle_time
                    outcome_price = target_price
                    break

                if protection_hit:
                    outcome = "PROTECTION"
                    outcome_time = candle_time
                    outcome_price = protection_price
                    break

            age_hours = max(
                0.0,
                (current_time - expired_at) / 3600,
            )

            checkpoints = follow.setdefault(
                "checkpoints",
                [],
            )
            existing_hours = {
                int(item.get("hour", 0))
                for item in checkpoints
                if isinstance(item, dict)
            }

            current_directional_r = (
                calculate_directional_r(
                    trade,
                    last_price,
                )
            )

            for checkpoint_hour in (
                POST_EXPIRY_CHECKPOINT_HOURS
            ):
                if (
                    age_hours >= checkpoint_hour
                    and checkpoint_hour
                    not in existing_hours
                ):
                    checkpoints.append({
                        "hour": checkpoint_hour,
                        "observed_at": current_time,
                        "price": last_price,
                        "directional_r": (
                            current_directional_r
                        ),
                    })
                    changed = True

            follow["last_checked_at"] = current_time
            follow["last_price"] = last_price
            follow["last_directional_r"] = (
                current_directional_r
            )
            trade["post_expiry_best_r"] = (
                follow.get("best_directional_r")
            )
            trade["post_expiry_worst_r"] = (
                follow.get("worst_directional_r")
            )
            changed = True

            if outcome is not None:
                finalize_post_expiry_follow(
                    trade,
                    follow,
                    outcome,
                    outcome_time or current_time,
                    outcome_price,
                )

            elif age_hours >= (
                POST_EXPIRY_MAX_TRACK_HOURS
            ):
                finalize_post_expiry_follow(
                    trade,
                    follow,
                    "NO_DECISION",
                    max_end_time,
                    last_price,
                )

            if (
                str(
                    follow.get("status", "")
                ).upper() == "RESOLVED"
                and not follow.get(
                    "telegram_notified",
                    False,
                )
            ):
                send_telegram(
                    build_post_expiry_telegram(
                        trade,
                        follow,
                    )
                )
                follow["telegram_notified"] = True
                changed = True

        except Exception as exc:
            print(
                trade_id,
                "süre sonrası takip hatası:",
                exc,
            )

    if changed:
        save_trade_ledger(ledger)



def restore_recent_sl_follow_for_extended_window(
    performance,
):
    """
    Eski 120 dakikalık sürümde NO_TP1_RETURN olarak kapatılmış,
    fakat yeni 240 dakikalık pencere içinde kalan SL işlemlerini
    yeniden takibe alır.

    trade_ledger.json içinde yakın tarihli bir SL kaydı varsa ve
    takip kaydı performance.json'dan temizlenmişse de yeniden kurar.
    """
    follow = performance.setdefault(
        "sl_after_follow",
        {},
    )
    ledger = load_trade_ledger()
    trades = ledger.get("trades", {})

    current_time = now_ts()
    existing_by_trade_id = {
        str(item.get("trade_id")): (key, item)
        for key, item in follow.items()
        if item.get("trade_id")
    }

    restored = 0

    for trade_id, trade in trades.items():
        try:
            if str(
                trade.get("final_result", "")
            ).upper() != "SL":
                continue

            stopped_at = int(
                trade.get("closed_at")
                or trade.get("trade_closed_at")
                or 0
            )

            if stopped_at <= 0:
                continue

            age_minutes = int(
                max(
                    0,
                    (current_time - stopped_at) / 60,
                )
            )

            if (
                age_minutes
                > SL_AFTER_MAX_TRACK_MINUTES
            ):
                continue

            post_stop = (
                trade.get("post_stop_follow")
                or {}
            )

            if str(
                post_stop.get("status", "")
            ).upper() == "RETURNED_TO_TARGET":
                continue

            existing = existing_by_trade_id.get(
                str(trade_id)
            )

            if existing:
                _, item = existing

                if (
                    item.get("resolved")
                    and not item.get("after_sl_tp1")
                    and age_minutes
                    < SL_AFTER_MAX_TRACK_MINUTES
                ):
                    item["resolved"] = False
                    item["extended_to_240"] = True
                    item.setdefault(
                        "reported_checkpoints",
                        [],
                    )

                    # Eski 120 dakika sonucu zaten sayılmış olabilir.
                    if (
                        str(
                            post_stop.get("status", "")
                        ).upper() == "NO_TP1_RETURN"
                    ):
                        item["no_return_counted"] = True
                        item["no_return_counted_day"] = (
                            day_key_from_ts(
                                int(
                                    post_stop.get(
                                        "updated_at"
                                    )
                                    or stopped_at
                                )
                            )
                        )

                    restored += 1

                continue

            # Takip kaydı daha önce temizlendiyse ledger'dan yeniden kur.
            key = (
                f"{trade.get('symbol')}_"
                f"{trade.get('direction')}_"
                f"{stopped_at}"
            )

            reported = []

            for checkpoint in (
                SL_AFTER_CHECKPOINT_MINUTES
            ):
                if (
                    checkpoint <= 120
                    and age_minutes >= checkpoint
                ):
                    reported.append(checkpoint)

            item = {
                "trade_id": trade_id,
                "symbol": trade.get("symbol"),
                "direction": trade.get("direction"),
                "source": trade.get("source"),
                "entry": trade.get("entry"),
                "tp1": trade.get("tp1"),
                "tp2": trade.get("tp2"),
                "tp3": trade.get("tp3"),
                "sl": trade.get("sl"),
                "score": trade.get("score"),
                "risk_percent": trade.get(
                    "risk_percent"
                ),
                "stopped_at": stopped_at,
                "stop_exit": trade.get("exit_price"),
                "reported_checkpoints": reported,
                "after_sl_tp1": False,
                "after_sl_tp2": False,
                "after_sl_tp3": False,
                "resolved": False,
                "extended_to_240": True,
            }

            if (
                str(
                    post_stop.get("status", "")
                ).upper() == "NO_TP1_RETURN"
            ):
                item["no_return_counted"] = True
                item["no_return_counted_day"] = (
                    day_key_from_ts(
                        int(
                            post_stop.get(
                                "updated_at"
                            )
                            or stopped_at
                        )
                    )
                )

            follow[key] = item
            existing_by_trade_id[
                str(trade_id)
            ] = (key, item)
            restored += 1

        except Exception as exc:
            print(
                trade_id,
                "Uzatılmış SL takibi geri yükleme hatası:",
                exc,
            )

    return restored


def undo_early_no_return_count(
    performance,
    item,
):
    """
    Eski 120 dakikalık sürümde 'dönmedi' sayılmış bir işlem,
    240 dakika içinde TP1'e dönerse eski sayımı geri alır.
    """
    if not item.get("no_return_counted"):
        return

    day_key = item.get(
        "no_return_counted_day"
    )

    if not day_key:
        day_key = today_key()

    day = performance.setdefault(
        "days",
        {},
    ).setdefault(
        day_key,
        {},
    )

    current = int(
        day.get("sl_after_no_return", 0)
    )

    if current > 0:
        day["sl_after_no_return"] = (
            current - 1
        )

    item["no_return_counted"] = False


def check_sl_after_follow(exchange):
    performance = ensure_perf_day(
        load_performance()
    )

    follow = performance.setdefault(
        "sl_after_follow",
        {},
    )

    restored_count = (
        restore_recent_sl_follow_for_extended_window(
            performance
        )
    )

    follow = performance.setdefault(
        "sl_after_follow",
        {},
    )

    if not follow:
        return

    changed = restored_count > 0

    if restored_count:
        print(
            "240 dakikalık pencereye yeniden alınan SL kaydı:",
            restored_count,
        )

    for key, item in list(follow.items()):
        try:
            if item.get("resolved"):
                continue

            symbol = item["symbol"]
            direction = item["direction"]
            entry = float(item["entry"])
            tp1 = float(item["tp1"])
            tp2 = float(item["tp2"])
            tp3 = float(item["tp3"])
            sl = float(item["sl"])

            stopped_at = int(
                item.get(
                    "stopped_at",
                    now_ts(),
                )
            )

            age_minutes = int(
                (
                    now_ts()
                    - stopped_at
                )
                / 60
            )

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                since_seconds=stopped_at,
                limit=max(
                    TRACK_LIMIT,
                    SL_AFTER_TRACK_LIMIT,
                ),
            )

            after_tp1 = False
            after_tp2 = False
            after_tp3 = False

            for candle in candles:
                high = float(candle["high"])
                low = float(candle["low"])

                if direction == "LONG":
                    after_tp1 = (
                        after_tp1
                        or high >= tp1
                    )
                    after_tp2 = (
                        after_tp2
                        or high >= tp2
                    )
                    after_tp3 = (
                        after_tp3
                        or high >= tp3
                    )
                else:
                    after_tp1 = (
                        after_tp1
                        or low <= tp1
                    )
                    after_tp2 = (
                        after_tp2
                        or low <= tp2
                    )
                    after_tp3 = (
                        after_tp3
                        or low <= tp3
                    )

            if (
                after_tp1
                and not item.get("after_sl_tp1")
            ):
                item["after_sl_tp1"] = True
                item["after_sl_tp2"] = bool(
                    after_tp2
                )
                item["after_sl_tp3"] = bool(
                    after_tp3
                )
                item["resolved"] = True
                changed = True

                undo_early_no_return_count(
                    performance,
                    item,
                )

                day = performance["days"].setdefault(
                    today_key(),
                    {},
                )

                day["sl_after_tp1"] = (
                    int(
                        day.get(
                            "sl_after_tp1",
                            0,
                        )
                    )
                    + 1
                )

                if after_tp2:
                    day["sl_after_tp2"] = (
                        int(
                            day.get(
                                "sl_after_tp2",
                                0,
                            )
                        )
                        + 1
                    )

                level_text = (
                    "TP3"
                    if after_tp3
                    else "TP2"
                    if after_tp2
                    else "TP1"
                )

                ledger_update_post_stop_diagnosis(
                    item.get("trade_id"),
                    returned_level=level_text,
                    age_minutes=age_minutes,
                )

                send_telegram(
                    f"📊 SL SONRASI TAKİP\n\n"
                    f"Coin: {symbol}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {format_price(entry)}\n"
                    f"SL: {format_price(sl)}\n"
                    f"Stop sonrası geçen süre: "
                    f"{age_minutes} dakika\n\n"
                    f"Sonuç: Stop sonrası fiyat "
                    f"{level_text} seviyesine döndü.\n"
                    f"Yorum: Stop dar kalmış veya "
                    f"fitil stop olmuş olabilir."
                )

                continue

            reported = item.setdefault(
                "reported_checkpoints",
                [],
            )

            for checkpoint in (
                SL_AFTER_CHECKPOINT_MINUTES
            ):
                if (
                    age_minutes >= checkpoint
                    and checkpoint not in reported
                ):
                    reported.append(checkpoint)
                    changed = True

                    if (
                        checkpoint
                        >= SL_AFTER_MAX_TRACK_MINUTES
                    ):
                        item["resolved"] = True

                        day = (
                            performance["days"]
                            .setdefault(
                                today_key(),
                                {},
                            )
                        )

                        if not item.get(
                            "no_return_counted"
                        ):
                            day["sl_after_no_return"] = (
                                int(
                                    day.get(
                                        "sl_after_no_return",
                                        0,
                                    )
                                )
                                + 1
                            )
                            item[
                                "no_return_counted"
                            ] = True
                            item[
                                "no_return_counted_day"
                            ] = today_key()

                        ledger_update_post_stop_diagnosis(
                            item.get("trade_id"),
                            returned_level=None,
                            age_minutes=age_minutes,
                        )

                    send_telegram(
                        f"📊 SL SONRASI TAKİP\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: {direction}\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"SL: {format_price(sl)}\n"
                        f"Kontrol: {checkpoint}. dakika\n\n"
                        f"Sonuç: Stop sonrası henüz "
                        f"TP1 seviyesine dönüş yok.\n"
                        f"Takip penceresi: "
                        f"{SL_AFTER_MAX_TRACK_MINUTES} dakika."
                    )
                    break

        except Exception as exc:
            print(
                key,
                "SL sonrası takip hatası:",
                exc,
            )

    for key, item in list(follow.items()):
        try:
            stopped_at = int(
                item.get("stopped_at", 0)
            )

            age = now_ts() - stopped_at

            if (
                item.get("resolved")
                and age
                > (
                    SL_AFTER_MAX_TRACK_MINUTES
                    + 60
                )
                * 60
            ):
                follow.pop(key, None)
                changed = True

        except Exception:
            pass

    if changed:
        performance["last_update"] = now_ts()
        save_performance(performance)


# =========================================================
# ACIK SINYAL TAKIBI
# =========================================================

def check_open_signals(exchange):
    open_signals = load_open_signals()

    if not open_signals:
        print("Açık sinyal yok.")
        return

    updated = {}
    max_age = (
        MAX_OPEN_SIGNAL_HOURS
        * 60
        * 60
    )

    for key, signal in open_signals.items():
        try:
            symbol = signal["symbol"]
            direction = signal["direction"]

            entry = float(signal["entry"])
            tp1 = float(signal["tp1"])
            tp2 = float(signal["tp2"])
            tp3 = float(signal["tp3"])
            sl = float(signal["sl"])

            opened_at = int(
                signal.get(
                    "opened_at",
                    now_ts(),
                )
            )

            last_checked_at = int(
                signal.get(
                    "last_checked_at",
                    opened_at,
                )
            )

            if (
                bool(signal.get("tp3_hit", False))
                or bool(signal.get("closed", False))
            ):
                print(
                    symbol,
                    "zaten kapanmış, takipten çıkarıldı.",
                )
                continue

            if now_ts() - opened_at > max_age:
                expiry_price = get_current_price(
                    exchange,
                    symbol,
                )

                # Güncel fiyat alınamazsa işlemi ölçümsüz kapatma.
                # Bir sonraki çalışmada yeniden kontrol edilir.
                if expiry_price is None:
                    updated[key] = signal
                    print(
                        symbol,
                        "süre doldu fakat güncel fiyat alınamadı.",
                    )
                    continue

                expiry_r = calculate_exit_r(
                    signal,
                    expiry_price,
                )

                expiry_r_text = (
                    f"{expiry_r:+.3f}R"
                    if expiry_r is not None
                    else "ölçülemedi"
                )

                send_telegram(
                    f"⏳ SİNYAL SÜRESİ DOLDU\n\n"
                    f"Coin: {symbol}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {format_price(entry)}\n"
                    f"Süre Sonu Fiyatı: "
                    f"{format_price(expiry_price)}\n"
                    f"Yaklaşık Net Sonuç: "
                    f"{expiry_r_text}\n\n"
                    f"{MAX_OPEN_SIGNAL_HOURS} saat içinde "
                    f"TP3/SL ile netleşmediği için "
                    f"takipten çıkarıldı."
                )

                close_signal_result(
                    symbol,
                    signal,
                    "EXPIRED",
                    expiry_price,
                )
                continue

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                since_seconds=max(
                    opened_at,
                    last_checked_at - 10 * 60,
                ),
                limit=TRACK_LIMIT,
            )

            if not candles:
                updated[key] = signal
                continue

            tp1_hit = bool(
                signal.get("tp1_hit", False)
            )

            # TP1'in görüldüğü mum sonraki workflow çalışmasında
            # tekrar okunabilir. Eski sinyallerde zaman kaydı yoksa
            # son kontrol zamanı güvenli başlangıç olarak kullanılır.
            tp1_hit_at = int(
                signal.get("tp1_hit_at")
                or (
                    last_checked_at
                    if tp1_hit
                    else 0
                )
            )

            if tp1_hit and not signal.get("tp1_hit_at"):
                signal["tp1_hit_at"] = tp1_hit_at

            tp2_hit = bool(
                signal.get("tp2_hit", False)
            )
            tp3_hit = bool(
                signal.get("tp3_hit", False)
            )

            closed = False

            for candle in candles:
                candle_time = int(
                    candle.get("time", 0)
                    or 0
                )
                high = float(candle["high"])
                low = float(candle["low"])
                close = float(candle["close"])

                update_signal_excursion(
                    signal,
                    high,
                    low,
                    candle_time=candle_time,
                )
                signal["last_market_price"] = close

                just_hit_tp1 = False

                if direction == "LONG":
                    if not tp1_hit:
                        if low <= sl and high >= tp1:
                            if close >= entry:
                                tp1_hit = True
                                just_hit_tp1 = True
                                signal["tp1_hit"] = True
                                tp1_hit_at = candle_time
                                signal["tp1_hit_at"] = tp1_hit_at

                                send_telegram(
                                    f"✅ TP1 GELDİ\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: LONG 🟢\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"TP1: {format_price(tp1)}\n"
                                    f"Öneri: %50 kâr al, "
                                    f"SL girişe çek."
                                )

                                register_partial_result(
                                    symbol,
                                    signal,
                                    "TP1",
                                    tp1,
                                )
                            else:
                                send_telegram(
                                    f"❌ STOP OLDU\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: LONG 🟢\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"SL: {format_price(sl)}\n"
                                    f"Güncel: {format_price(close)}"
                                )

                                close_signal_result(
                                    symbol,
                                    signal,
                                    "SL",
                                    close,
                                )

                                closed = True
                                break

                        elif low <= sl:
                            send_telegram(
                                f"❌ STOP OLDU\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: LONG 🟢\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"SL: {format_price(sl)}\n"
                                f"Güncel: {format_price(close)}"
                            )

                            close_signal_result(
                                symbol,
                                signal,
                                "SL",
                                close,
                            )

                            closed = True
                            break

                        elif high >= tp1:
                            tp1_hit = True
                            just_hit_tp1 = True
                            signal["tp1_hit"] = True
                            tp1_hit_at = candle_time
                            signal["tp1_hit_at"] = tp1_hit_at

                            send_telegram(
                                f"✅ TP1 GELDİ\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: LONG 🟢\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"TP1: {format_price(tp1)}\n"
                                f"Öneri: %50 kâr al, "
                                f"SL girişe çek."
                            )

                            register_partial_result(
                                symbol,
                                signal,
                                "TP1",
                                tp1,
                            )

                    if (
                        tp1_hit
                        and not tp2_hit
                        and high >= tp2
                    ):
                        tp2_hit = True
                        signal["tp2_hit"] = True

                        send_telegram(
                            f"✅ TP2 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: LONG 🟢\n"
                            f"TP2: {format_price(tp2)}"
                        )

                        register_partial_result(
                            symbol,
                            signal,
                            "TP2",
                            tp2,
                        )

                    if (
                        tp1_hit
                        and not tp3_hit
                        and high >= tp3
                    ):
                        tp3_hit = True
                        signal["tp3_hit"] = True
                        signal["closed"] = True

                        send_telegram(
                            f"🏁 TP3 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: LONG 🟢\n"
                            f"TP3: {format_price(tp3)}\n"
                            f"Sinyal maksimum hedefe ulaştı."
                        )

                        close_signal_result(
                            symbol,
                            signal,
                            "TP3",
                            tp3,
                        )

                        closed = True
                        break

                    if (
                        tp1_hit
                        and not just_hit_tp1
                        and candle_time > tp1_hit_at
                        and low <= entry
                    ):
                        signal["closed"] = True

                        send_telegram(
                            f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: LONG 🟢\n"
                            f"Giriş: {format_price(entry)}"
                        )

                        close_signal_result(
                            symbol,
                            signal,
                            "BE",
                            entry,
                        )

                        closed = True
                        break

                else:
                    if not tp1_hit:
                        if high >= sl and low <= tp1:
                            if close <= entry:
                                tp1_hit = True
                                just_hit_tp1 = True
                                signal["tp1_hit"] = True
                                tp1_hit_at = candle_time
                                signal["tp1_hit_at"] = tp1_hit_at

                                send_telegram(
                                    f"✅ TP1 GELDİ\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: SHORT 🔴\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"TP1: {format_price(tp1)}\n"
                                    f"Öneri: %50 kâr al, "
                                    f"SL girişe çek."
                                )

                                register_partial_result(
                                    symbol,
                                    signal,
                                    "TP1",
                                    tp1,
                                )
                            else:
                                send_telegram(
                                    f"❌ STOP OLDU\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: SHORT 🔴\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"SL: {format_price(sl)}\n"
                                    f"Güncel: {format_price(close)}"
                                )

                                close_signal_result(
                                    symbol,
                                    signal,
                                    "SL",
                                    close,
                                )

                                closed = True
                                break

                        elif high >= sl:
                            send_telegram(
                                f"❌ STOP OLDU\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: SHORT 🔴\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"SL: {format_price(sl)}\n"
                                f"Güncel: {format_price(close)}"
                            )

                            close_signal_result(
                                symbol,
                                signal,
                                "SL",
                                close,
                            )

                            closed = True
                            break

                        elif low <= tp1:
                            tp1_hit = True
                            just_hit_tp1 = True
                            signal["tp1_hit"] = True
                            tp1_hit_at = candle_time
                            signal["tp1_hit_at"] = tp1_hit_at

                            send_telegram(
                                f"✅ TP1 GELDİ\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: SHORT 🔴\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"TP1: {format_price(tp1)}\n"
                                f"Öneri: %50 kâr al, "
                                f"SL girişe çek."
                            )

                            register_partial_result(
                                symbol,
                                signal,
                                "TP1",
                                tp1,
                            )

                    if (
                        tp1_hit
                        and not tp2_hit
                        and low <= tp2
                    ):
                        tp2_hit = True
                        signal["tp2_hit"] = True

                        send_telegram(
                            f"✅ TP2 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"TP2: {format_price(tp2)}"
                        )

                        register_partial_result(
                            symbol,
                            signal,
                            "TP2",
                            tp2,
                        )

                    if (
                        tp1_hit
                        and not tp3_hit
                        and low <= tp3
                    ):
                        tp3_hit = True
                        signal["tp3_hit"] = True
                        signal["closed"] = True

                        send_telegram(
                            f"🏁 TP3 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"TP3: {format_price(tp3)}\n"
                            f"Sinyal maksimum hedefe ulaştı."
                        )

                        close_signal_result(
                            symbol,
                            signal,
                            "TP3",
                            tp3,
                        )

                        closed = True
                        break

                    if (
                        tp1_hit
                        and not just_hit_tp1
                        and candle_time > tp1_hit_at
                        and high >= entry
                    ):
                        signal["closed"] = True

                        send_telegram(
                            f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"Giriş: {format_price(entry)}"
                        )

                        close_signal_result(
                            symbol,
                            signal,
                            "BE",
                            entry,
                        )

                        closed = True
                        break

            if closed:
                continue

            signal["tp1_hit"] = tp1_hit
            signal["tp1_hit_at"] = tp1_hit_at
            signal["tp2_hit"] = tp2_hit
            signal["tp3_hit"] = tp3_hit
            signal["last_checked_at"] = now_ts()

            ledger_update_open_snapshot(
                signal
            )

            updated[key] = signal

        except Exception as exc:
            print(
                key,
                "açık sinyal takip hatası:",
                exc,
            )
            updated[key] = signal

    save_open_signals(updated)


# =========================================================
# DURUM / GUNLUK RAPOR
# =========================================================

def should_send_status():
    performance = load_performance()

    last_status = int(
        performance.get(
            "last_status_message",
            0,
        )
    )

    return (
        now_ts() - last_status
        >= SEND_STATUS_EVERY_MINUTES * 60
    )


def mark_status_sent():
    performance = load_performance()
    performance["last_status_message"] = now_ts()
    save_performance(performance)


def maybe_send_open_summary(exchange):
    performance = load_performance()

    last_summary = int(
        performance.get(
            "last_open_summary",
            0,
        )
    )

    if (
        now_ts() - last_summary
        < OPEN_SUMMARY_EVERY_MINUTES * 60
    ):
        return

    open_signals = load_open_signals()

    if not open_signals:
        return

    lines = ["📌 AÇIK SİNYAL ÖZETİ\n"]

    for signal in list(
        open_signals.values()
    )[:10]:
        try:
            symbol = signal["symbol"]
            direction = signal["direction"]
            entry = float(signal["entry"])
            tp1 = float(signal["tp1"])
            sl = float(signal["sl"])

            current = get_current_price(
                exchange,
                symbol,
            )

            if current is None:
                continue

            if direction == "LONG":
                profit = (
                    (current - entry)
                    / entry
                    * 100
                )
                tp_distance = (
                    (tp1 - current)
                    / current
                    * 100
                )
                icon = "🟢"
            else:
                profit = (
                    (entry - current)
                    / entry
                    * 100
                )
                tp_distance = (
                    (current - tp1)
                    / current
                    * 100
                )
                icon = "🔴"

            lines.append(
                f"{icon} {symbol} {direction}\n"
                f"Giriş: {format_price(entry)} | "
                f"Güncel: {format_price(current)}\n"
                f"TP1: {format_price(tp1)} | "
                f"SL: {format_price(sl)}\n"
                f"Durum: %{round(profit, 2)} | "
                f"TP1 uzaklık: "
                f"%{round(tp_distance, 2)}\n"
            )

        except Exception as exc:
            print(
                "Özet satır hatası:",
                exc,
            )

    send_telegram("\n".join(lines))

    performance["last_open_summary"] = now_ts()
    save_performance(performance)


def build_daily_report():
    performance = load_performance()

    day = (
        performance
        .get("days", {})
        .get(today_key(), {})
    )

    opened = int(day.get("opened", 0))
    radar = int(day.get("radar", 0))
    tp1 = int(day.get("tp1", 0))
    tp2 = int(day.get("tp2", 0))
    tp3 = int(day.get("tp3", 0))
    sl = int(day.get("sl", 0))
    be = int(day.get("be", 0))
    expired = int(day.get("expired", 0))
    long_count = int(day.get("long", 0))
    short_count = int(day.get("short", 0))
    normal_count = int(day.get("normal", 0))
    radar_trade = int(
        day.get("radar_trade", 0)
    )

    sl_after_tp1 = int(
        day.get("sl_after_tp1", 0)
    )
    sl_after_tp2 = int(
        day.get("sl_after_tp2", 0)
    )
    sl_after_no_return = int(
        day.get("sl_after_no_return", 0)
    )

    open_count = len(
        load_open_signals()
    )

    closed = tp1 + sl

    success = (
        round(
            tp1 / closed * 100,
            2,
        )
        if closed > 0
        else 0
    )

    coins = day.get("coins", {})

    best_coin = "Yok"
    worst_coin = "Yok"
    best_rate = -1
    worst_rate = 101

    for coin, stats in coins.items():
        c_tp1 = int(
            stats.get("tp1", 0)
        )
        c_sl = int(
            stats.get("sl", 0)
        )
        c_closed = c_tp1 + c_sl

        if c_closed <= 0:
            continue

        rate = round(
            c_tp1 / c_closed * 100,
            2,
        )

        if rate > best_rate:
            best_rate = rate
            best_coin = (
                f"{coin} (%{rate})"
            )

        if rate < worst_rate:
            worst_rate = rate
            worst_coin = (
                f"{coin} (%{rate})"
            )

    recent_lines = []

    for item in day.get(
        "closed_history",
        [],
    )[-8:]:
        recent_lines.append(
            f"{item.get('time')} | "
            f"{item.get('symbol')} "
            f"{item.get('direction')} "
            f"→ {item.get('result')}"
        )

    recent_text = (
        "\n".join(recent_lines)
        if recent_lines
        else "Henüz kapanan işlem yok."
    )

    return f"""📊 GÜNLÜK PERFORMANS RAPORU

Tarih: {today_key()}

Açılan İşlem Sinyali: {opened}
Radar Uyarısı: {radar}
LONG: {long_count}
SHORT: {short_count}

✅ 15M Giriş: {normal_count}
⚡ 5M Radar Trade: {radar_trade}

✅ TP1 Gelen: {tp1}
✅ TP2 Gelen: {tp2}
🏁 TP3 Gelen: {tp3}
🟡 Girişten Kapanan: {be}
❌ Stop Olan: {sl}
⏳ Süresi Dolan: {expired}
📌 Açık Sinyal: {open_count}

📊 TP1 Başarı Oranı: %{success}

🧪 SL Sonrası TP1'e Dönen: {sl_after_tp1}
🧪 SL Sonrası TP2'ye Dönen: {sl_after_tp2}
🧪 SL Sonrası Dönmeyen: {sl_after_no_return}

🏆 En İyi Coin: {best_coin}
⚠️ En Zayıf Coin: {worst_coin}

Son Kapananlar:
{recent_text}

Not:
Bu eski olay raporudur.
Gerçek tekil işlem ve net R sonucu ayrı v3 raporunda gösterilir.
Bu bot emir açmaz, sadece sinyal gönderir."""


def maybe_send_daily_report():
    now = datetime.now(TR_TIMEZONE)
    today = today_key()

    if (
        now.hour != DAILY_REPORT_HOUR
        or now.minute < DAILY_REPORT_MINUTE
    ):
        return

    performance = load_performance()

    if performance.get(
        "last_daily_report"
    ) == today:
        return

    # Eski olay raporu Telegram'a gönderilmez.
    # Tek kaynaklı birleşik Net R + teşhis raporu gönderilir.
    send_telegram(build_daily_r_report())

    performance["last_daily_report"] = today
    save_performance(performance)


# =========================================================
# YENI SINYAL KAYDI
# =========================================================

def save_open_signal(signal):
    open_signals = load_open_signals()
    opened_at = now_ts()

    trade_id = (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal.get('source', 'MTF')}_"
        f"{opened_at}"
    )

    key = (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal.get('source', 'MTF')}"
    )

    saved_signal = {
        "trade_id": trade_id,
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "source": signal.get(
            "source",
            "MTF",
        ),
        "entry": signal["entry"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "tp3": signal["tp3"],
        "sl": signal["sl"],
        "score": signal["score"],
        "risk_percent": signal.get(
            "risk_percent"
        ),
        "quality": signal.get("quality"),
        "quality_note": signal.get("quality_note"),
        "trend_reason": signal.get("trend_reason"),
        "confirm_reason": signal.get("confirm_reason"),
        "entry_reason": signal.get("entry_reason"),
        "radar_reason": signal.get("radar_reason"),
        "rsi_15m": signal.get("rsi_15m"),
        "adx_15m": signal.get("adx_15m"),
        "adx_4h": signal.get("adx_4h"),
        "adx_1h": signal.get("adx_1h"),
        "volume_ratio": signal.get("volume_ratio"),
        "ideal_entry": signal.get("ideal_entry"),
        "zone_name": signal.get("zone_name"),
        "zone_distance_percent": signal.get(
            "zone_distance_percent"
        ),
        "rr_tp1": signal.get("rr_tp1"),
        "rr_tp2": signal.get("rr_tp2"),
        "rr_tp3": signal.get("rr_tp3"),
        "leverage": signal.get("leverage"),
        "sent_price": signal.get("sent_price"),
        "entry_distance_at_send_percent": signal.get(
            "entry_distance_at_send_percent"
        ),
        "tp1_progress_at_send_percent": signal.get(
            "tp1_progress_at_send_percent"
        ),
        "market_guard_long_allowed": signal.get(
            "market_guard_long_allowed"
        ),
        "market_guard_short_allowed": signal.get(
            "market_guard_short_allowed"
        ),
        "market_guard_reason": signal.get(
            "market_guard_reason"
        ),
        "best_favorable_percent": 0.0,
        "worst_adverse_percent": 0.0,
        "best_favorable_r": 0.0,
        "worst_adverse_r": 0.0,
        "best_favorable_price": signal.get("entry"),
        "worst_adverse_price": signal.get("entry"),
        "last_market_price": signal.get("sent_price"),
        "opened_at": opened_at,
        "last_checked_at": opened_at,
        "tp1_hit": False,
        "tp1_hit_at": 0,
        "tp2_hit": False,
        "tp3_hit": False,
        "closed": False,
    }

    open_signals[key] = saved_signal
    save_open_signals(open_signals)

    ensure_ledger_trade(saved_signal)


# =========================================================
# MAIN
# =========================================================

def main():
    print(BOT_NAME, "başladı.")

    sync_open_signals_to_ledger()

    exchange = get_exchange()

    check_open_signals(exchange)
    check_sl_after_follow(exchange)
    check_post_expiry_follow(exchange)
    maybe_send_open_summary(exchange)

    risk_mode = risk_mode_active()

    if risk_mode:
        print(
            "Risk modu aktif. "
            "Sistem durmadı, daha seçici çalışıyor."
        )

    scan_coins = get_scan_coins(exchange)
    market_status = get_market_direction_status(
        exchange
    )

    risky_open, reduced_open, total_open = (
        count_open_signal_risk()
    )

    print("Taranan coin:", len(scan_coins))
    print("Açık sinyal:", total_open)
    print("Riskli açık sinyal:", risky_open)
    print(
        "TP1 görmüş takipte sinyal:",
        reduced_open,
    )
    print("Risk modu:", risk_mode)

    trade_candidates = []
    radar_candidates = []

    for symbol in scan_coins:
        try:
            if has_open_same_symbol(symbol):
                print(
                    symbol,
                    "zaten açık sinyal var, atlandı.",
                )
                continue

            if has_recent_stop(symbol):
                print(
                    symbol,
                    "yakın zamanda stop olduğu için atlandı.",
                )
                continue

            if has_recent_closed_signal(symbol):
                print(
                    symbol,
                    "yakın zamanda kapandığı için tekrar sinyal atlandı.",
                )
                continue

            current_price = get_current_price(
                exchange,
                symbol,
            )

            df15m = fetch_df(
                exchange,
                symbol,
                ENTRY_TIMEFRAME,
                ENTRY_LIMIT,
                min_len=120,
            )

            df1h = fetch_df(
                exchange,
                symbol,
                CONFIRM_TIMEFRAME,
                CONFIRM_LIMIT,
                min_len=120,
            )

            df4h = fetch_df(
                exchange,
                symbol,
                TREND_TIMEFRAME,
                TREND_LIMIT,
                min_len=120,
            )

            normal_signal = analyze_mtf_trade(
                symbol,
                df15m,
                df1h,
                df4h,
                current_price,
            )

            signals = []

            if normal_signal is not None:
                signals.append(
                    normal_signal
                )

            df5m = fetch_df(
                exchange,
                symbol,
                RADAR_TIMEFRAME,
                RADAR_LIMIT,
                min_len=50,
            )

            radar_signal = analyze_5m_radar(
                symbol,
                df5m,
                df15m,
                df1h,
                df4h,
                current_price,
            )

            if radar_signal is not None:
                signals.append(
                    radar_signal
                )

            for signal in signals:
                direction = signal["direction"]

                if (
                    direction == "LONG"
                    and not ALLOW_LONG
                ):
                    continue

                if (
                    direction == "SHORT"
                    and not ALLOW_SHORT
                ):
                    continue

                if (
                    signal.get("signal_class")
                    == "TRADE"
                    and not market_status.get(
                        direction,
                        True,
                    )
                ):
                    print(
                        symbol,
                        "market yönü ters: trade -> radar",
                        direction,
                    )
                    signal["signal_class"] = "RADAR"

                if (
                    risk_mode
                    and signal.get("source")
                    == "5M_RADAR"
                    and signal.get("signal_class")
                    == "TRADE"
                    and not RISK_MODE_ALLOW_RADAR_TRADE
                ):
                    signal["signal_class"] = "RADAR"

                valid, reason = (
                    is_entry_still_valid(
                        signal,
                        current_price,
                    )
                )

                if not valid:
                    print(
                        symbol,
                        "giriş elendi ->",
                        reason,
                    )
                    continue

                if (
                    signal.get("signal_class")
                    == "TRADE"
                ):
                    if not is_duplicate(
                        signal,
                        radar=False,
                    ):
                        trade_candidates.append(
                            signal
                        )
                        print(
                            symbol,
                            "A kalite aday:",
                            signal.get("source"),
                            direction,
                            signal.get("score"),
                        )
                else:
                    if not is_duplicate(
                        signal,
                        radar=True,
                    ):
                        radar_candidates.append(
                            signal
                        )

            time.sleep(0.10)

        except Exception as exc:
            print(
                symbol,
                "analiz hatası:",
                exc,
            )

    trade_candidates.sort(
        key=lambda signal: (
            signal.get("score", 0),
            1
            if signal.get("source")
            == "15M_ENTRY"
            else 0,
        ),
        reverse=True,
    )

    radar_candidates.sort(
        key=lambda signal: signal.get(
            "score",
            0,
        ),
        reverse=True,
    )

    max_trade = (
        RISK_MODE_MAX_TRADE_SIGNALS
        if risk_mode
        else MAX_TRADE_SIGNALS_PER_RUN
    )

    max_radar = (
        RISK_MODE_MAX_RADAR_ALERTS
        if risk_mode
        else MAX_RADAR_ALERTS_PER_RUN
    )

    risky_open, reduced_open, _ = (
        count_open_signal_risk()
    )

    available_trade_slots = max(
        0,
        MAX_OPEN_SIGNALS - risky_open,
    )

    allowed_trade_count = min(
        max_trade,
        available_trade_slots,
    )

    selected_trade = trade_candidates[
        :allowed_trade_count
    ]

    selected_limit_watch = []

    if (
        available_trade_slots <= 0
        and trade_candidates
    ):
        # Limit dolu takip mesajı gerçek TRADE duplicate kaydını
        # kirletmez; ayrı RADAR anahtarıyla 45 dakika tekrar engellenir.
        limit_watch_candidates = [
            signal
            for signal in trade_candidates
            if not is_duplicate(
                signal,
                radar=True,
            )
        ]

        selected_limit_watch = (
            limit_watch_candidates[:1]
        )

    selected_radar = radar_candidates[
        :max_radar
    ]

    if selected_trade:
        send_telegram(
            f"✅ {BOT_NAME} çalıştı.\n"
            f"Taranan coin: {len(scan_coins)}\n"
            f"A kalite aday: {len(trade_candidates)}\n"
            f"Riskli açık sinyal: "
            f"{risky_open}/{MAX_OPEN_SIGNALS}\n"
            f"TP1 görmüş takipte sinyal: "
            f"{reduced_open}\n"
            f"Son kontrole seçilen işlem adayı: "
            f"{len(selected_trade)}\n"
            f"Risk Modu: "
            f"{'AKTİF' if risk_mode else 'Kapalı'}\n"
            f"Sistem: {SYSTEM_NOTE}"
        )

    if selected_limit_watch:
        send_telegram(
            f"⚠️ {BOT_NAME} güçlü aday buldu "
            f"ama riskli açık sinyal sınırı dolu.\n"
            f"Riskli açık sinyal: "
            f"{risky_open}/{MAX_OPEN_SIGNALS}\n"
            f"TP1 görmüş takipte sinyal: "
            f"{reduced_open}\n"
            f"Yeni işlem kaydı açılmadı; "
            f"yalnızca takip uyarısı gönderilecek."
        )

    for signal in selected_trade:
        current_price = get_current_price(
            exchange,
            signal["symbol"],
        )

        valid, reason = is_entry_still_valid(
            signal,
            current_price,
        )

        if not valid:
            print(
                signal["symbol"],
                "son kontrol elendi:",
                reason,
            )
            continue

        entry_price = safe_float(
            signal.get("entry")
        )
        tp1_price = safe_float(
            signal.get("tp1")
        )

        entry_distance_at_send = None
        tp1_progress_at_send = None

        if (
            entry_price is not None
            and entry_price > 0
            and current_price is not None
        ):
            entry_distance_at_send = abs(
                current_price - entry_price
            ) / entry_price * 100

            if (
                tp1_price is not None
                and signal.get("direction") == "LONG"
                and tp1_price > entry_price
            ):
                tp1_progress_at_send = (
                    current_price - entry_price
                ) / (
                    tp1_price - entry_price
                ) * 100

            elif (
                tp1_price is not None
                and signal.get("direction") == "SHORT"
                and tp1_price < entry_price
            ):
                tp1_progress_at_send = (
                    entry_price - current_price
                ) / (
                    entry_price - tp1_price
                ) * 100

        signal["sent_price"] = current_price
        signal[
            "entry_distance_at_send_percent"
        ] = (
            round(entry_distance_at_send, 4)
            if entry_distance_at_send is not None
            else None
        )
        signal[
            "tp1_progress_at_send_percent"
        ] = (
            round(tp1_progress_at_send, 4)
            if tp1_progress_at_send is not None
            else None
        )
        signal["market_guard_long_allowed"] = (
            market_status.get("LONG")
        )
        signal["market_guard_short_allowed"] = (
            market_status.get("SHORT")
        )
        signal["market_guard_reason"] = (
            market_status.get("reason")
        )

        extra = (
            f"\n💰 Güncel Fiyat: "
            f"{format_price(current_price)}\n"
            f"📌 Son Kontrol: Girişe yakın ✅"
        )

        if send_telegram(
            signal["message"] + extra
        ):
            save_open_signal(signal)
            mark_sent(
                signal,
                radar=False,
            )

            update_performance(
                signal["symbol"],
                "OPENED",
                direction=signal["direction"],
                source=signal.get("source"),
                entry=signal.get("entry"),
                score=signal.get("score"),
            )

            time.sleep(1)

    for signal in selected_limit_watch:
        current_price = get_current_price(
            exchange,
            signal["symbol"],
        )

        valid, reason = is_entry_still_valid(
            signal,
            current_price,
        )

        if not valid:
            print(
                signal["symbol"],
                "limit takip son kontrol elendi:",
                reason,
            )
            continue

        watch_message = build_limit_watch_message(
            signal,
            current_price=current_price,
        )

        if send_telegram(watch_message):
            mark_sent(
                signal,
                radar=True,
            )
            time.sleep(1)

    if selected_radar:
        send_telegram(
            f"📡 {BOT_NAME} radar çalıştı.\n"
            f"Radar uyarısı: {len(selected_radar)}\n"
            f"Bu mesajlar işlem sinyali değildir."
        )

    for signal in selected_radar:
        radar_message = signal["message"].replace(
            "A KALİTE MTF FUTURES SİNYALİ",
            "5M / 15M RADAR - İŞLEM AÇMA",
        )

        if send_telegram(radar_message):
            mark_sent(
                signal,
                radar=True,
            )

            update_performance(
                signal["symbol"],
                "RADAR",
                direction=signal["direction"],
                source=signal.get("source"),
                entry=signal.get("entry"),
                score=signal.get("score"),
            )

            time.sleep(1)

    if (
        not selected_trade
        and not selected_radar
        and not selected_limit_watch
    ):
        print("Uygun sinyal yok.")

        if should_send_status():
            risky_open, reduced_open, _ = (
                count_open_signal_risk()
            )

            send_telegram(
                f"📡 {BOT_NAME} çalıştı.\n\n"
                f"Taranan coin: {len(scan_coins)}\n"
                f"Uygun MTF sinyali yok.\n"
                f"Riskli açık sinyal: "
                f"{risky_open}/{MAX_OPEN_SIGNALS}\n"
                f"TP1 görmüş takipte sinyal: "
                f"{reduced_open}\n"
                f"Risk Modu: "
                f"{'AKTİF' if risk_mode else 'Kapalı'}\n"
                f"Sistem durmadı, taramaya devam ediyor."
            )

            mark_status_sent()

    maybe_send_daily_report()

    print(BOT_NAME, "tamamlandı.")


if __name__ == "__main__":
    main()