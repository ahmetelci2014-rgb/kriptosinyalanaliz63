# swing_radar.py
# Swing Radar v3 - 15M Erken Giris + Performans + Teknik Teshis
#
# OKX USDT perpetual futures:
# - 1D ana trend
# - 4H ana yapi
# - 1H normal swing onayi
# - 15M ilk red / ilk devam zamanlamasi
#
# Emir acmaz. Telegram sinyali gonderir ve TP/SL takibi yapar.
#
# v3 yenilikleri:
# - Eski 1H onayli Swing yolu aynen korunur.
# - 1H mum kapanmadan once, yalnizca cok guclu kurulumlarda
#   15M erken giris yolu kullanilir.
# - Erken giris icin skor esigi ve 15M filtreleri daha siktir.
# - Tek calismada en fazla 1 yeni Swing sinyali.
# - En fazla 3 acik Swing sinyali.
# - Gonderimden hemen once giris bolgesi tekrar kontrol edilir.
# - TP1'in geldigi ayni mumda yanlis breakeven kapanisi engellenir.
# - Eski swing_radar_state.json kayitlariyla uyumludur.
# - Telegram API yanit govdesi loglanmaz.
# - State ve performans JSON dosyalari dogrulamali atomik yazilir.
# - Ortak portfoy denetimi ayni coindeki bot cakismalarini engeller.

import json
import math
import os
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import ccxt
import pandas as pd
import requests

from portfolio_risk import (
    evaluate_portfolio_risk,
    format_portfolio_note,
)


# =========================================================
# GENEL AYARLAR
# =========================================================

BOT_NAME = "Swing Radar v3 - 15M Erken Giris"

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

STATE_FILE = "swing_radar_state.json"
PERFORMANCE_FILE = "swing_performance_ledger.json"
TR_TIMEZONE = timezone(timedelta(hours=3))

# Gönderilen gerçek Swing sinyallerinin yön performansı ve
# TP/SL sonuçları ayrı dosyada saklanır.
PERFORMANCE_WINDOWS_MINUTES = (
    30,
    60,
    240,
    720,
    1440,
)
PERFORMANCE_DIRECTION_FINAL_MINUTES = 1440
PERFORMANCE_KEEP_DAYS = 45
PERFORMANCE_MAX_RECORDS = 400
PERFORMANCE_DIRECTION_THRESHOLD_PERCENT = 1.00
PERFORMANCE_MIXED_THRESHOLD_PERCENT = 0.40

# Swing teknik teshisi:
# Stop sonrasi yonun gecikmeli gelmesi 48 saate kadar izlenir.
SWING_DIAGNOSIS_VERSION = "SWING_DIAGNOSIS_V1"
POST_STOP_CHECKPOINT_MINUTES = (60, 240, 720, 1440, 2880)
POST_STOP_MAX_TRACK_MINUTES = 2880
POST_STOP_KEEP_HOURS = 72

MAX_SCAN_COINS = 220
MIN_24H_QUOTE_VOLUME = 500_000

MAX_NEW_SIGNALS_PER_RUN = 1
MAX_OPEN_SWING_SIGNALS = 3

DUPLICATE_SECONDS = 18 * 60 * 60

# Açık sinyal takibi 5M mumlarla yapılır.
# Böylece aynı 15M mum içinde TP ve SL görülmesi durumunda
# oluşabilecek sıra belirsizliği azaltılır.
TRACK_TIMEFRAME = "5m"
TRACK_LIMIT = 420
MAX_OPEN_SIGNAL_HOURS = 120

SEND_NO_SIGNAL_REPORT = True
NO_SIGNAL_REPORT_EVERY_MINUTES = 360

# Normal 1H onayli yol ve daha siki 15M erken yol.
MIN_SCORE_NORMAL = 80
MIN_SCORE_EARLY = 88

MIN_RISK_PERCENT = 0.80
MAX_RISK_PERCENT = 3.00

TP1_R = 0.80
TP2_R = 1.60
TP3_R = 2.50

MAX_DISTANCE_FROM_1H_EMA20_PERCENT = 3.20
MAX_DISTANCE_FROM_4H_EMA20_PERCENT = 5.50

# Erken giriste fiyat 15M EMA20'den fazla kopmus olmamali.
MAX_EARLY_DISTANCE_FROM_15M_EMA20_PERCENT = 1.20

MIN_ADX_1H = 16
MIN_ADX_4H = 15
MIN_VOLUME_RATIO = 0.75

# 15M erken giris icin daha dusuk hacim, ancak diger tum
# onaylar birlikte zorunludur.
MIN_EARLY_15M_VOLUME_RATIO = 0.70

# Gönderim anında fiyat giriş bölgesinden ne kadar taşabilir?
MAX_ENTRY_ZONE_DRIFT_PERCENT = 0.50
MAX_EARLY_ENTRY_ZONE_DRIFT_PERCENT = 0.35

# Tarama sırasında yön yapısı bozulursa eski analize dayanarak
# sinyal gönderilmez. Bu toleranslar yönü değiştirmez;
# yalnız geçersizleşen adayı engeller.
FINAL_NORMAL_DIRECTION_TOLERANCE_PERCENT = 0.80
FINAL_EARLY_DIRECTION_TOLERANCE_PERCENT = 0.30

D1_LIMIT = 260
H4_LIMIT = 260
H1_LIMIT = 260
M15_LIMIT = 260


# =========================================================
# TELEGRAM
# =========================================================

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


# =========================================================
# STATE
# =========================================================

def now_ts():
    return int(time.time())


def tr_now_text():
    return datetime.now(TR_TIMEZONE).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def normalize_bot_symbol(symbol):
    value = str(symbol or "").upper().strip()
    value = value.replace("/USDT:USDT", "USDT")
    value = value.replace(":USDT", "")
    value = value.replace("/", "")

    if value and not value.endswith("USDT"):
        value += "USDT"

    return value


def empty_stats():
    return {
        "signals": 0,
        "normal_signals": 0,
        "early_signals": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "stop": 0,
        "breakeven": 0,
        "expired": 0,
    }


def empty_state():
    return {
        "open_swing_signals": {},
        "last_sent": {},
        "last_no_signal_report": 0,
        "post_stop_follow": {},
        "stats": empty_stats(),
    }


def load_state():
    try:
        if not os.path.exists(STATE_FILE):
            return empty_state()

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as handle:
            raw = handle.read().strip()

        if not raw:
            return empty_state()

        state = json.loads(raw)

        if not isinstance(state, dict):
            state = empty_state()

        state.setdefault("open_swing_signals", {})
        state.setdefault("last_sent", {})
        state.setdefault("last_no_signal_report", 0)
        state.setdefault("post_stop_follow", {})
        state.setdefault("stats", {})

        for key, value in empty_stats().items():
            state["stats"].setdefault(key, value)

        migrated = {}

        for old_key, signal in state[
            "open_swing_signals"
        ].items():
            if not isinstance(signal, dict):
                continue

            item = dict(signal)
            item["symbol"] = normalize_bot_symbol(
                item.get("symbol")
            )

            opened_at = int(
                item.get("opened_at")
                or item.get("created_ts")
                or now_ts()
            )

            item["opened_at"] = opened_at
            item["last_checked_at"] = int(
                item.get("last_checked_at")
                or opened_at
            )
            item.setdefault("tp1_hit", False)
            item.setdefault("tp1_hit_at", 0)
            item.setdefault("tp2_hit", False)
            item.setdefault("tp3_hit", False)
            item.setdefault("closed", False)
            item.setdefault("best_favorable_percent", 0.0)
            item.setdefault("worst_adverse_percent", 0.0)
            item.setdefault("best_favorable_r", 0.0)
            item.setdefault("worst_adverse_r", 0.0)
            item.setdefault(
                "timing_mode",
                "1H_ONAYLI",
            )

            new_key = (
                f"{item.get('symbol', '')}_"
                f"{item.get('direction', '')}_"
                f"{item.get('source', 'SWING_RADAR')}"
            )

            migrated[new_key or old_key] = item

        state["open_swing_signals"] = migrated
        return state

    except Exception as exc:
        print("State okuma hatası:", exc)
        return empty_state()



def fsync_parent_directory(filename):
    """
    os.replace sonrasında klasör kaydını da diske zorlamaya çalışır.
    Desteklenmeyen ortamlarda ana JSON kaydı yine geçerli kalır.
    """
    directory = os.path.dirname(
        os.path.abspath(filename)
    ) or "."

    directory_fd = None

    try:
        directory_fd = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(directory_fd)

    except Exception:
        pass

    finally:
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except Exception:
                pass


def atomic_save_json(filename, data):
    """
    JSON'u aynı klasörde geçici dosyaya yazar, doğrular ve
    os.replace ile tek adımda asıl dosyanın yerine geçirir.
    """
    normalized_data = (
        data
        if isinstance(data, dict)
        else {}
    )

    absolute_filename = os.path.abspath(
        filename
    )
    directory = os.path.dirname(
        absolute_filename
    ) or "."
    base_name = os.path.basename(
        absolute_filename
    )
    temp_path = None

    try:
        os.makedirs(
            directory,
            exist_ok=True,
        )

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{base_name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name

            json.dump(
                normalized_data,
                handle,
                indent=2,
                ensure_ascii=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(
            temp_path,
            "r",
            encoding="utf-8",
        ) as verify_handle:
            verified = json.load(
                verify_handle
            )

        if not isinstance(
            verified,
            dict,
        ):
            raise ValueError(
                "Geçici JSON doğrulaması başarısız."
            )

        os.replace(
            temp_path,
            absolute_filename,
        )
        temp_path = None

        fsync_parent_directory(
            absolute_filename
        )

        return True

    except Exception as exc:
        print(
            filename,
            "atomik JSON kaydetme hatası:",
            exc,
        )
        return False

    finally:
        if (
            temp_path
            and os.path.exists(temp_path)
        ):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def save_state(state):
    try:
        normalized_state = (
            state
            if isinstance(state, dict)
            else empty_state()
        )

        return atomic_save_json(
            STATE_FILE,
            normalized_state,
        )

    except Exception as exc:
        print(
            "State kaydetme hatası:",
            exc,
        )
        return False


def increment_stat(state, key):
    state.setdefault("stats", empty_stats())
    state["stats"][key] = (
        int(state["stats"].get(key, 0))
        + 1
    )



# =========================================================
# SWING PERFORMANS KAYDI
# =========================================================

def load_swing_performance():
    try:
        if not os.path.exists(PERFORMANCE_FILE):
            return {
                "records": [],
                "summary": {},
            }

        with open(
            PERFORMANCE_FILE,
            "r",
            encoding="utf-8",
        ) as handle:
            raw = handle.read().strip()

        if not raw:
            return {
                "records": [],
                "summary": {},
            }

        ledger = json.loads(raw)

        if not isinstance(ledger, dict):
            ledger = {}

        ledger.setdefault("records", [])
        ledger.setdefault("summary", {})
        return ledger

    except Exception as exc:
        print(
            "Swing performans dosyası okuma hatası:",
            exc,
        )
        return {
            "records": [],
            "summary": {},
        }


def rebuild_swing_performance_summary(ledger):
    records = ledger.get("records", [])

    summary = {
        "total": len(records),
        "direction_open": 0,
        "direction_correct": 0,
        "direction_wrong": 0,
        "direction_mixed": 0,
        "trade_open": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "stop": 0,
        "breakeven": 0,
        "expired": 0,
        "early_15m": 0,
        "confirmed_1h": 0,
        "long": 0,
        "short": 0,
        "diagnosis_open": 0,
        "diagnosis_success": 0,
        "diagnosis_early_failed": 0,
        "diagnosis_delayed_direction": 0,
        "diagnosis_weak_trend": 0,
        "diagnosis_setup_failed": 0,
    }

    for record in records:
        direction = str(
            record.get("direction", "")
        ).upper()

        if direction == "LONG":
            summary["long"] += 1
        elif direction == "SHORT":
            summary["short"] += 1

        timing_mode = str(
            record.get(
                "timing_mode",
                "1H_ONAYLI",
            )
        ).upper()

        if timing_mode == "15M_ERKEN":
            summary["early_15m"] += 1
        else:
            summary["confirmed_1h"] += 1

        direction_status = str(
            record.get(
                "direction_status",
                "OPEN",
            )
        ).upper()

        if direction_status == "OPEN":
            summary["direction_open"] += 1
        elif direction_status == "DIRECTION_CORRECT":
            summary["direction_correct"] += 1
        elif direction_status == "DIRECTION_WRONG":
            summary["direction_wrong"] += 1
        elif direction_status == "MIXED":
            summary["direction_mixed"] += 1

        trade_outcome = str(
            record.get(
                "trade_outcome",
                "OPEN",
            )
        ).upper()

        if trade_outcome == "OPEN":
            summary["trade_open"] += 1
        elif trade_outcome == "TP1":
            summary["tp1"] += 1
        elif trade_outcome == "TP2":
            summary["tp2"] += 1
        elif trade_outcome == "TP3":
            summary["tp3"] += 1
        elif trade_outcome == "STOP":
            summary["stop"] += 1
        elif trade_outcome == "BREAKEVEN":
            summary["breakeven"] += 1
        elif trade_outcome == "EXPIRED":
            summary["expired"] += 1

        diagnosis = record.get("diagnosis") or {}
        diagnosis_code = str(
            diagnosis.get("code", "OPEN")
        ).upper()

        if diagnosis_code == "OPEN":
            summary["diagnosis_open"] += 1
        elif diagnosis_code == "SWING_SUCCESS":
            summary["diagnosis_success"] += 1
        elif diagnosis_code == "EARLY_ENTRY_FAILED":
            summary["diagnosis_early_failed"] += 1
        elif diagnosis_code in (
            "DIRECTION_RIGHT_ENTRY_EARLY",
            "DIRECTION_RECOVERED_LATE",
            "DIRECTION_RIGHT_STOP_TIGHT",
        ):
            summary["diagnosis_delayed_direction"] += 1
        elif diagnosis_code == "WEAK_TREND_OR_VOLUME":
            summary["diagnosis_weak_trend"] += 1
        elif diagnosis_code in (
            "SETUP_FAILED",
            "NO_SWING_CONTINUATION",
        ):
            summary["diagnosis_setup_failed"] += 1

    ledger["summary"] = summary
    ledger["updated_at"] = now_ts()
    ledger["updated_at_tr"] = tr_now_text()


def save_swing_performance(ledger):
    try:
        rebuild_swing_performance_summary(
            ledger
        )

        return atomic_save_json(
            PERFORMANCE_FILE,
            ledger,
        )

    except Exception as exc:
        print(
            "Swing performans dosyası kayıt hatası:",
            exc,
        )
        return False


def swing_directional_move_percent(
    direction,
    current_price,
    reference_price,
):
    current = safe_float(current_price)
    reference = safe_float(reference_price)

    if current <= 0 or reference <= 0:
        return 0.0

    raw_move = (
        current - reference
    ) / reference * 100

    if str(direction).upper() == "LONG":
        return raw_move

    return -raw_move


def classify_swing_direction(record):
    snapshots = record.get(
        "snapshots",
        {},
    )

    move_24h = safe_float(
        snapshots.get("1440m")
    )
    best_favorable = safe_float(
        record.get(
            "best_favorable_percent"
        )
    )
    worst_adverse = safe_float(
        record.get(
            "worst_adverse_percent"
        )
    )

    threshold = (
        PERFORMANCE_DIRECTION_THRESHOLD_PERCENT
    )
    mixed_threshold = (
        PERFORMANCE_MIXED_THRESHOLD_PERCENT
    )

    if (
        move_24h >= threshold
        or (
            best_favorable >= threshold
            and worst_adverse < threshold
        )
    ):
        return (
            "DIRECTION_CORRECT",
            "24 saat içinde Swing yönü desteklendi",
        )

    if (
        move_24h <= -threshold
        or (
            worst_adverse >= threshold
            and best_favorable < threshold
        )
    ):
        return (
            "DIRECTION_WRONG",
            "24 saat içinde fiyat Swing yönünün tersine gitti",
        )

    if abs(move_24h) <= mixed_threshold:
        return (
            "MIXED",
            "24 saat sonunda belirgin Swing yönü oluşmadı",
        )

    return (
        (
            "DIRECTION_CORRECT"
            if move_24h > 0
            else "DIRECTION_WRONG"
        ),
        "24 saat son fiyatına göre sınıflandırıldı",
    )



def swing_record_by_id(ledger, record_id):
    if not record_id:
        return None

    for record in ledger.get("records", []):
        if str(record.get("id")) == str(record_id):
            return record

    return None


def apply_signal_metrics_to_swing_record(record, signal):
    if record is None or not isinstance(signal, dict):
        return

    fields = (
        "timing_mode",
        "setup",
        "zone_drift",
        "direction_check",
        "rsi_d1",
        "rsi_4h",
        "rsi_1h",
        "rsi_15m",
        "adx_4h",
        "adx_1h",
        "vol_4h",
        "vol_1h",
        "vol_15m",
        "dist_1h_ema20",
        "dist_4h_ema20",
        "dist_15m_ema20",
        "d1_note",
        "h4_note",
        "h1_note",
        "m15_note",
        "ok_count",
        "total_conditions",
        "missing",
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

    for field in fields:
        value = signal.get(field)

        if value is not None:
            record[field] = value

    record["tp1_hit"] = bool(
        signal.get(
            "tp1_hit",
            record.get("tp1_hit", False),
        )
    )
    record["tp2_hit"] = bool(
        signal.get(
            "tp2_hit",
            record.get("tp2_hit", False),
        )
    )
    record["tp3_hit"] = bool(
        signal.get(
            "tp3_hit",
            record.get("tp3_hit", False),
        )
    )


def update_swing_excursion(signal, high, low, candle_time=None):
    entry = safe_float(signal.get("entry"))
    sl = safe_float(signal.get("sl"))
    high = safe_float(high)
    low = safe_float(low)

    if (
        entry <= 0
        or sl <= 0
        or high <= 0
        or low <= 0
    ):
        return

    risk = abs(entry - sl)

    if risk <= 0:
        return

    direction = str(
        signal.get("direction", "")
    ).upper()

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
        favorable_r = max(
            0.0,
            (high - entry) / risk,
        )
        adverse_r = max(
            0.0,
            (entry - low) / risk,
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
        favorable_r = max(
            0.0,
            (entry - low) / risk,
        )
        adverse_r = max(
            0.0,
            (high - entry) / risk,
        )

    else:
        return

    if favorable_percent > safe_float(
        signal.get("best_favorable_percent")
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
        signal.get("worst_adverse_percent")
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


def build_swing_diagnosis(record):
    outcome = str(
        record.get("trade_outcome", "OPEN")
    ).upper()
    timing_mode = str(
        record.get("timing_mode", "1H_ONAYLI")
    ).upper()

    sent_at = int(
        record.get("sent_at") or now_ts()
    )
    closed_at = int(
        record.get("trade_closed_at")
        or record.get("trade_last_updated_at")
        or now_ts()
    )
    duration_minutes = int(
        max(0, (closed_at - sent_at) / 60)
    )

    mfe_r = safe_float(
        record.get("best_favorable_r")
    )
    mae_r = safe_float(
        record.get("worst_adverse_r")
    )
    adx_4h = safe_float(record.get("adx_4h"))
    adx_1h = safe_float(record.get("adx_1h"))
    vol_4h = safe_float(record.get("vol_4h"))
    vol_1h = safe_float(record.get("vol_1h"))
    vol_15m = safe_float(record.get("vol_15m"))
    zone_drift = safe_float(
        record.get("zone_drift")
    )
    dist_15m = safe_float(
        record.get("dist_15m_ema20")
    )

    diagnosis = {
        "version": SWING_DIAGNOSIS_VERSION,
        "code": "OPEN",
        "primary": "Swing işlem sonucu bekleniyor",
        "confidence": "DÜŞÜK",
        "factors": [],
        "duration_minutes": duration_minutes,
        "best_favorable_r": round(mfe_r, 4),
        "worst_adverse_r": round(mae_r, 4),
        "provisional": outcome in (
            "OPEN",
            "TP1",
            "TP2",
            "STOP",
        ),
        "note": (
            "Bu teşhis kesin piyasa sebebi değildir; "
            "kayıtlı zamanlama, trend, hacim ve fiyat "
            "hareketine dayalı teknik değerlendirmedir."
        ),
    }
    factors = diagnosis["factors"]

    if outcome == "TP3":
        diagnosis["code"] = "SWING_SUCCESS"
        diagnosis["primary"] = (
            "SWING YÖNÜ VE ZAMANLAMASI BAŞARILI"
        )
        diagnosis["confidence"] = "YÜKSEK"
        diagnosis["provisional"] = False
        factors.append(
            "İşlem maksimum hedef TP3'e ulaştı."
        )
        return diagnosis

    if outcome == "BREAKEVEN":
        diagnosis["code"] = "DIRECTION_CORRECT"
        diagnosis["primary"] = (
            "YÖN DOĞRUYDU, DEVAM GÜCÜ ZAYIFLADI"
        )
        diagnosis["confidence"] = "YÜKSEK"
        diagnosis["provisional"] = False
        factors.append(
            "TP1 sonrası kalan bölüm girişten kapandı."
        )
        return diagnosis

    if outcome in ("TP1", "TP2"):
        diagnosis["code"] = "DIRECTION_CORRECT"
        diagnosis["primary"] = (
            "SWING YÖNÜ DOĞRU, İŞLEM DEVAM EDİYOR"
        )
        diagnosis["confidence"] = "YÜKSEK"
        factors.append(
            f"İşlem {outcome} seviyesine ulaştı."
        )
        return diagnosis

    if outcome == "EXPIRED":
        result_r = safe_float(
            record.get("trade_result_r")
        )

        if result_r > 0:
            diagnosis["code"] = "DIRECTION_RECOVERED_LATE"
            diagnosis["primary"] = (
                "YÖN KISMEN DOĞRUYDU, HEDEF GEÇ KALDI"
            )
        else:
            diagnosis["code"] = "NO_SWING_CONTINUATION"
            diagnosis["primary"] = (
                "120 SAATTE BELİRGİN SWING DEVAMI OLUŞMADI"
            )

        diagnosis["confidence"] = "ORTA"
        diagnosis["provisional"] = False
        factors.append(
            "Maksimum Swing takip süresi tamamlandı."
        )
        return diagnosis

    if outcome != "STOP":
        return diagnosis

    diagnosis["confidence"] = "ORTA"

    if (
        timing_mode == "15M_ERKEN"
        and duration_minutes <= 360
        and mfe_r < 0.20
    ):
        diagnosis["code"] = "EARLY_ENTRY_FAILED"
        diagnosis["primary"] = (
            "15M ERKEN GİRİŞ BAŞARISIZ / 1H ONAYI GELMEDİ"
        )
        factors.append(
            "Erken giriş ilk 6 saatte anlamlı lehe hareket yapmadan stop oldu."
        )

    elif mfe_r >= 0.35:
        diagnosis["code"] = "DIRECTION_CORRECT"
        diagnosis["primary"] = (
            "ÖNCE LEHE GİTTİ, SONRA SWING YAPISI BOZULDU"
        )
        factors.append(
            "Stop öncesinde işlem en az 0.35R lehe hareket etti."
        )

    elif (
        min(adx_4h, adx_1h) < 16
        or max(vol_4h, vol_1h) < 0.85
    ):
        diagnosis["code"] = "WEAK_TREND_OR_VOLUME"
        diagnosis["primary"] = (
            "TREND / HACİM DEVAMI ZAYIF KALDI"
        )
        factors.append(
            "Üst zaman trend gücü veya hacim devamı sınırdaydı."
        )

    else:
        diagnosis["code"] = "SETUP_FAILED"
        diagnosis["primary"] = (
            "SWING KURULUMU DEVAM ETMEDİ"
        )
        factors.append(
            "Kurulum yeterli lehe hareket oluşturmadan stop oldu."
        )

    if zone_drift > 0.30:
        factors.append(
            "Gönderim anında fiyat giriş bölgesinden uzaklaşmıştı."
        )

    if timing_mode == "15M_ERKEN":
        if vol_15m < 0.90:
            factors.append(
                "15M erken giriş hacmi devam için sınırdaydı."
            )

        if dist_15m > 0.80:
            factors.append(
                "15M erken giriş EMA20'den göreceli olarak uzaktı."
            )

    if mae_r >= 1.0 and duration_minutes <= 360:
        factors.append(
            "Stop mesafesi ilk 6 saat içinde tamamen tüketildi."
        )

    return diagnosis


def sync_swing_open_metrics(signal):
    record_id = signal.get("performance_record_id")

    if not record_id:
        return False

    try:
        ledger = load_swing_performance()
        record = swing_record_by_id(
            ledger,
            record_id,
        )

        if record is None:
            return False

        apply_signal_metrics_to_swing_record(
            record,
            signal,
        )
        return save_swing_performance(ledger)

    except Exception as exc:
        print(
            "Swing açık metrik senkron hatası:",
            exc,
        )
        return False


def update_swing_post_stop_diagnosis(
    record_id,
    status,
    returned_level=None,
    age_minutes=None,
):
    if not record_id:
        return False

    try:
        ledger = load_swing_performance()
        record = swing_record_by_id(
            ledger,
            record_id,
        )

        if record is None:
            return False

        diagnosis = (
            record.get("diagnosis")
            or build_swing_diagnosis(record)
        )
        diagnosis.setdefault("factors", [])

        timing_mode = str(
            record.get("timing_mode", "1H_ONAYLI")
        ).upper()

        if status == "TARGET_RETURN":
            if timing_mode == "15M_ERKEN":
                diagnosis["code"] = (
                    "DIRECTION_RIGHT_ENTRY_EARLY"
                )
                diagnosis["primary"] = (
                    "YÖN DOĞRUYDU, 15M GİRİŞ ERKEN KALDI"
                )
            else:
                diagnosis["code"] = (
                    "DIRECTION_RIGHT_STOP_TIGHT"
                )
                diagnosis["primary"] = (
                    "YÖN DOĞRUYDU, STOP ERKEN / DAR KALDI"
                )

            diagnosis["confidence"] = "YÜKSEK"
            diagnosis["provisional"] = False
            diagnosis["factors"].append(
                f"Stop sonrası fiyat {returned_level} seviyesine ulaştı."
            )

        elif status == "ENTRY_RECOVERY":
            diagnosis["code"] = (
                "DIRECTION_RECOVERED_LATE"
            )
            diagnosis["primary"] = (
                "YÖN SONRADAN TOPARLANDI, TP1 GELMEDİ"
            )
            diagnosis["confidence"] = "ORTA"
            diagnosis["provisional"] = False
            diagnosis["factors"].append(
                "Stop sonrası fiyat giriş seviyesini geri aldı fakat TP1'e ulaşmadı."
            )

        else:
            diagnosis["provisional"] = False
            diagnosis["factors"].append(
                "Stop sonrası 48 saat içinde giriş veya TP1 yönlü dönüş oluşmadı."
            )

        record["post_stop_follow"] = {
            "status": status,
            "returned_level": returned_level,
            "age_minutes": age_minutes,
            "updated_at": now_ts(),
            "updated_at_tr": tr_now_text(),
        }
        record["diagnosis"] = diagnosis
        return save_swing_performance(ledger)

    except Exception as exc:
        print(
            "Swing stop sonrası teşhis güncelleme hatası:",
            exc,
        )
        return False


def add_swing_post_stop_follow(state, signal, exit_price):
    record_id = signal.get("performance_record_id")

    if not record_id:
        return

    state.setdefault("post_stop_follow", {})
    stopped_at = now_ts()

    state["post_stop_follow"][str(record_id)] = {
        "record_id": record_id,
        "symbol": normalize_bot_symbol(
            signal.get("symbol")
        ),
        "direction": signal.get("direction"),
        "timing_mode": signal.get(
            "timing_mode",
            "1H_ONAYLI",
        ),
        "entry": signal.get("entry"),
        "tp1": signal.get("tp1"),
        "tp2": signal.get("tp2"),
        "tp3": signal.get("tp3"),
        "sl": signal.get("sl"),
        "stop_exit": exit_price,
        "stopped_at": stopped_at,
        "last_checked_at": stopped_at,
        "reported_checkpoints": [],
        "recovered_entry": False,
        "reached_tp1": False,
        "reached_tp2": False,
        "reached_tp3": False,
        "resolved": False,
    }
    save_state(state)


def check_swing_post_stop_follow(exchange, state):
    follow = state.setdefault(
        "post_stop_follow",
        {},
    )

    if not follow:
        return

    changed = False

    for key, item in list(follow.items()):
        try:
            if item.get("resolved"):
                continue

            symbol = normalize_bot_symbol(
                item.get("symbol")
            )
            direction = str(
                item.get("direction", "")
            ).upper()
            entry = safe_float(item.get("entry"))
            tp1 = safe_float(item.get("tp1"))
            tp2 = safe_float(item.get("tp2"))
            tp3 = safe_float(item.get("tp3"))
            stopped_at = int(
                item.get("stopped_at") or now_ts()
            )
            last_checked_at = int(
                item.get("last_checked_at")
                or stopped_at
            )
            age_minutes = int(
                max(
                    0,
                    (now_ts() - stopped_at) / 60,
                )
            )

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                since_seconds=max(
                    stopped_at,
                    last_checked_at - 30 * 60,
                ),
                limit=TRACK_LIMIT,
            )

            for candle in candles:
                high = safe_float(candle.get("high"))
                low = safe_float(candle.get("low"))

                if direction == "LONG":
                    item["recovered_entry"] = bool(
                        item.get("recovered_entry")
                        or high >= entry
                    )
                    item["reached_tp1"] = bool(
                        item.get("reached_tp1")
                        or high >= tp1
                    )
                    item["reached_tp2"] = bool(
                        item.get("reached_tp2")
                        or high >= tp2
                    )
                    item["reached_tp3"] = bool(
                        item.get("reached_tp3")
                        or high >= tp3
                    )

                elif direction == "SHORT":
                    item["recovered_entry"] = bool(
                        item.get("recovered_entry")
                        or low <= entry
                    )
                    item["reached_tp1"] = bool(
                        item.get("reached_tp1")
                        or low <= tp1
                    )
                    item["reached_tp2"] = bool(
                        item.get("reached_tp2")
                        or low <= tp2
                    )
                    item["reached_tp3"] = bool(
                        item.get("reached_tp3")
                        or low <= tp3
                    )

            item["last_checked_at"] = now_ts()

            if item.get("reached_tp1"):
                returned_level = (
                    "TP3"
                    if item.get("reached_tp3")
                    else "TP2"
                    if item.get("reached_tp2")
                    else "TP1"
                )
                update_swing_post_stop_diagnosis(
                    item.get("record_id"),
                    status="TARGET_RETURN",
                    returned_level=returned_level,
                    age_minutes=age_minutes,
                )
                item["resolved"] = True
                item["returned_level"] = returned_level
                item["resolved_at"] = now_ts()
                changed = True
                continue

            reported = item.setdefault(
                "reported_checkpoints",
                [],
            )

            for checkpoint in POST_STOP_CHECKPOINT_MINUTES:
                if (
                    age_minutes >= checkpoint
                    and checkpoint not in reported
                ):
                    reported.append(checkpoint)
                    changed = True

            if age_minutes >= POST_STOP_MAX_TRACK_MINUTES:
                if item.get("recovered_entry"):
                    status = "ENTRY_RECOVERY"
                else:
                    status = "NO_RETURN"

                update_swing_post_stop_diagnosis(
                    item.get("record_id"),
                    status=status,
                    returned_level=None,
                    age_minutes=age_minutes,
                )
                item["resolved"] = True
                item["resolved_at"] = now_ts()
                changed = True

        except Exception as exc:
            print(
                key,
                "Swing stop sonrası takip hatası:",
                exc,
            )

    keep_seconds = POST_STOP_KEEP_HOURS * 60 * 60

    for key, item in list(follow.items()):
        stopped_at = int(item.get("stopped_at", 0))

        if (
            item.get("resolved")
            and now_ts() - stopped_at > keep_seconds
        ):
            follow.pop(key, None)
            changed = True

    if changed:
        save_state(state)


def record_swing_performance(signal):
    try:
        ledger = load_swing_performance()
        records = ledger.setdefault(
            "records",
            [],
        )

        sent_at = now_ts()
        symbol = normalize_bot_symbol(
            signal.get("symbol")
        )
        direction = str(
            signal.get("direction", "")
        ).upper()

        reference_price = safe_float(
            signal.get(
                "current_price",
                signal.get("entry"),
            )
        )

        if (
            not symbol
            or direction not in ("LONG", "SHORT")
            or reference_price <= 0
        ):
            return False

        record_id = (
            f"{symbol}_{direction}_"
            f"{signal.get('timing_mode', '1H_ONAYLI')}_"
            f"{sent_at}"
        )

        record = {
            "id": record_id,
            "stage": "REAL_SIGNAL",
            "symbol": symbol,
            "direction": direction,
            "source": signal.get("source"),
            "timing_mode": signal.get(
                "timing_mode",
                "1H_ONAYLI",
            ),
            "setup": signal.get("setup"),
            "sent_at": sent_at,
            "sent_at_tr": tr_now_text(),
            "reference_price": reference_price,
            "analysis_entry": safe_float(
                signal.get("entry")
            ),
            "entry": safe_float(
                signal.get("entry")
            ),
            "entry_low": safe_float(
                signal.get("entry_low")
            ),
            "entry_high": safe_float(
                signal.get("entry_high")
            ),
            "tp1": safe_float(
                signal.get("tp1")
            ),
            "tp2": safe_float(
                signal.get("tp2")
            ),
            "tp3": safe_float(
                signal.get("tp3")
            ),
            "sl": safe_float(
                signal.get("sl")
            ),
            "risk_percent": safe_float(
                signal.get("risk_percent")
            ),
            "score": safe_float(
                signal.get("score")
            ),
            "minimum_score": safe_float(
                signal.get("minimum_score")
            ),
            "rsi_d1": safe_float(
                signal.get("rsi_d1")
            ),
            "rsi_4h": safe_float(
                signal.get("rsi_4h")
            ),
            "dist_1h_ema20": safe_float(
                signal.get("dist_1h_ema20")
            ),
            "dist_4h_ema20": safe_float(
                signal.get("dist_4h_ema20")
            ),
            "dist_15m_ema20": safe_float(
                signal.get("dist_15m_ema20")
            ),
            "d1_note": signal.get("d1_note"),
            "h4_note": signal.get("h4_note"),
            "h1_note": signal.get("h1_note"),
            "m15_note": signal.get("m15_note"),
            "ok_count": signal.get("ok_count"),
            "total_conditions": signal.get(
                "total_conditions"
            ),
            "missing": list(
                signal.get("missing") or []
            ),
            "zone_drift": safe_float(
                signal.get("zone_drift")
            ),
            "direction_check": signal.get(
                "direction_check"
            ),
            "rsi_1h": safe_float(
                signal.get("rsi_1h")
            ),
            "rsi_15m": safe_float(
                signal.get("rsi_15m")
            ),
            "adx_4h": safe_float(
                signal.get("adx_4h")
            ),
            "adx_1h": safe_float(
                signal.get("adx_1h")
            ),
            "vol_4h": safe_float(
                signal.get("vol_4h")
            ),
            "vol_1h": safe_float(
                signal.get("vol_1h")
            ),
            "vol_15m": safe_float(
                signal.get("vol_15m")
            ),
            "snapshots": {},
            "latest_price": reference_price,
            "latest_directional_move_percent": 0.0,
            "best_favorable_percent": 0.0,
            "worst_adverse_percent": 0.0,
            "best_favorable_r": 0.0,
            "worst_adverse_r": 0.0,
            "best_favorable_price": reference_price,
            "worst_adverse_price": reference_price,
            "last_market_price": reference_price,
            "direction_status": "OPEN",
            "direction_reason": (
                "Gönderim sonrası Swing yönü izleniyor"
            ),
            "trade_outcome": "OPEN",
            "trade_result_r": None,
            "milestones": [],
            "diagnosis": {
                "version": SWING_DIAGNOSIS_VERSION,
                "code": "OPEN",
                "primary": "Swing işlem sonucu bekleniyor",
                "confidence": "DÜŞÜK",
                "factors": [],
                "provisional": True,
            },
            "post_stop_follow": None,
        }

        records.append(record)

        cutoff = (
            now_ts()
            - PERFORMANCE_KEEP_DAYS
            * 24
            * 60
            * 60
        )

        records = [
            item
            for item in records
            if int(
                item.get("sent_at", 0)
            ) >= cutoff
        ]

        records.sort(
            key=lambda item: int(
                item.get("sent_at", 0)
            )
        )

        ledger["records"] = records[
            -PERFORMANCE_MAX_RECORDS:
        ]

        if save_swing_performance(ledger):
            return record_id

        return False

    except Exception as exc:
        print(
            "Swing performans kaydı oluşturma hatası:",
            exc,
        )
        return False


def update_swing_trade_outcome(
    symbol,
    direction,
    outcome,
    current_price=None,
    result_r=None,
):
    try:
        ledger = load_swing_performance()
        records = ledger.setdefault(
            "records",
            [],
        )

        symbol = normalize_bot_symbol(
            symbol
        )
        direction = str(
            direction
        ).upper()
        outcome = str(outcome).upper()

        eligible = [
            record
            for record in records
            if normalize_bot_symbol(
                record.get("symbol")
            ) == symbol
            and str(
                record.get("direction", "")
            ).upper() == direction
            and str(
                record.get(
                    "trade_outcome",
                    "OPEN",
                )
            ).upper()
            not in (
                "TP3",
                "STOP",
                "BREAKEVEN",
                "EXPIRED",
            )
        ]

        if not eligible:
            return False

        record = max(
            eligible,
            key=lambda item: int(
                item.get("sent_at", 0)
            ),
        )

        current = safe_float(
            current_price
        )

        if result_r is None and current > 0:
            result_r = calculate_open_r(
                direction,
                record.get("entry"),
                record.get("sl"),
                current,
            )

        milestone = {
            "outcome": outcome,
            "time": now_ts(),
            "time_tr": tr_now_text(),
            "price": (
                current
                if current > 0
                else None
            ),
            "result_r": result_r,
        }

        record.setdefault(
            "milestones",
            [],
        ).append(milestone)

        record["trade_outcome"] = outcome
        record["trade_result_r"] = result_r
        record["trade_last_updated_at"] = (
            now_ts()
        )
        record["trade_last_updated_at_tr"] = (
            tr_now_text()
        )

        if outcome in (
            "TP3",
            "STOP",
            "BREAKEVEN",
            "EXPIRED",
        ):
            record["trade_closed_at"] = now_ts()
            record["trade_closed_at_tr"] = (
                tr_now_text()
            )

        record["diagnosis"] = build_swing_diagnosis(
            record
        )

        return save_swing_performance(ledger)

    except Exception as exc:
        print(
            "Swing işlem sonucu kayıt hatası:",
            exc,
        )
        return False


def update_swing_performance(exchange):
    ledger = load_swing_performance()
    records = ledger.setdefault(
        "records",
        [],
    )

    if not records:
        save_swing_performance(ledger)
        print(
            "Swing performans kaydı: açık gözlem yok."
        )
        return

    active_records = [
        record
        for record in records
        if str(
            record.get(
                "direction_status",
                "OPEN",
            )
        ).upper() == "OPEN"
    ]

    symbols = sorted({
        normalize_bot_symbol(
            record.get("symbol")
        )
        for record in active_records
        if normalize_bot_symbol(
            record.get("symbol")
        )
    })

    price_map = {}

    if symbols:
        try:
            okx_symbols = [
                to_okx_symbol(symbol)
                for symbol in symbols
            ]

            tickers = exchange.fetch_tickers(
                okx_symbols
            )

            for symbol in symbols:
                ticker = tickers.get(
                    to_okx_symbol(symbol),
                    {},
                )
                price = ticker.get("last")

                if price is not None:
                    price_map[symbol] = float(price)

        except Exception as exc:
            print(
                "Swing performans toplu fiyat hatası:",
                exc,
            )

    updated_count = 0
    finalized_count = 0

    for record in active_records:
        symbol = normalize_bot_symbol(
            record.get("symbol")
        )
        current_price = price_map.get(
            symbol
        )

        if current_price is None:
            continue

        sent_at = int(
            record.get("sent_at", now_ts())
        )
        age_minutes = max(
            0,
            (now_ts() - sent_at) / 60,
        )

        move = swing_directional_move_percent(
            record.get("direction"),
            current_price,
            record.get("reference_price"),
        )

        record["latest_price"] = (
            current_price
        )
        record[
            "latest_directional_move_percent"
        ] = round(move, 4)
        record["last_updated_at"] = (
            now_ts()
        )
        record["last_updated_at_tr"] = (
            tr_now_text()
        )

        record[
            "best_favorable_percent"
        ] = round(
            max(
                safe_float(
                    record.get(
                        "best_favorable_percent"
                    )
                ),
                move,
                0.0,
            ),
            4,
        )

        record[
            "worst_adverse_percent"
        ] = round(
            max(
                safe_float(
                    record.get(
                        "worst_adverse_percent"
                    )
                ),
                -move,
                0.0,
            ),
            4,
        )

        snapshots = record.setdefault(
            "snapshots",
            {},
        )

        for window in (
            PERFORMANCE_WINDOWS_MINUTES
        ):
            key = f"{window}m"

            if (
                age_minutes >= window
                and key not in snapshots
            ):
                snapshots[key] = round(
                    move,
                    4,
                )

        if (
            age_minutes
            >= PERFORMANCE_DIRECTION_FINAL_MINUTES
        ):
            status, reason = (
                classify_swing_direction(
                    record
                )
            )

            record["direction_status"] = (
                status
            )
            record["direction_reason"] = (
                reason
            )
            record[
                "direction_finalized_at"
            ] = now_ts()
            record[
                "direction_finalized_at_tr"
            ] = tr_now_text()
            finalized_count += 1

        updated_count += 1

    cutoff = (
        now_ts()
        - PERFORMANCE_KEEP_DAYS
        * 24
        * 60
        * 60
    )

    ledger["records"] = [
        record
        for record in records
        if int(
            record.get("sent_at", 0)
        ) >= cutoff
    ][-PERFORMANCE_MAX_RECORDS:]

    save_swing_performance(ledger)

    print(
        "Swing performans güncellendi:",
        updated_count,
        "| yönü sonuçlanan:",
        finalized_count,
    )


# =========================================================
# OKX / VERI
# =========================================================

def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap",
        },
    })


def to_okx_symbol(symbol):
    bot_symbol = normalize_bot_symbol(symbol)
    base = (
        bot_symbol[:-4]
        if bot_symbol.endswith("USDT")
        else bot_symbol
    )
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
                or "/USDT:USDT"
                not in okx_symbol
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
            volume = safe_quote_volume(
                tickers.get(okx_symbol, {})
            )

            if volume >= MIN_24H_QUOTE_VOLUME:
                rows.append((
                    okx_symbol_to_bot_symbol(
                        okx_symbol
                    ),
                    volume,
                ))

        rows.sort(
            key=lambda item: item[1],
            reverse=True,
        )

        coins = [
            symbol
            for symbol, _ in rows[
                :MAX_SCAN_COINS
            ]
        ]

        print(
            "Taranacak swing coin sayısı:",
            len(coins),
        )
        print("İlk 20:", coins[:20])

        return coins

    except Exception as exc:
        print("Coin tarama hatası:", exc)
        return []


def fetch_df(
    exchange,
    symbol,
    timeframe,
    limit=200,
    min_len=60,
):
    try:
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            limit=limit,
        )

        if not ohlcv or len(ohlcv) < min_len:
            return None

        frame = pd.DataFrame(
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

        for column in (
            "open",
            "high",
            "low",
            "close",
            "volume",
        ):
            frame[column] = pd.to_numeric(
                frame[column],
                errors="coerce",
            )

        frame = (
            frame
            .dropna()
            .reset_index(drop=True)
        )

        return (
            frame
            if len(frame) >= min_len
            else None
        )

    except Exception as exc:
        print(
            symbol,
            timeframe,
            "veri hatası:",
            exc,
        )
        return None


def fetch_candles_since(
    exchange,
    symbol,
    timeframe,
    since_seconds,
    limit=420,
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
                "volume": float(item[5]),
            }
            for item in ohlcv
        ]

    except Exception as exc:
        print(
            symbol,
            "mum takip hatası:",
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
# HESAPLAMALAR
# =========================================================

def safe_float(value, default=0.0):
    try:
        number = float(value)

        if (
            math.isnan(number)
            or math.isinf(number)
        ):
            return default

        return number

    except Exception:
        return default


def format_price(value):
    number = safe_float(value)

    if number >= 100:
        return f"{number:.2f}"

    if number >= 10:
        return f"{number:.3f}"

    if number >= 1:
        return f"{number:.4f}"

    if number >= 0.1:
        return f"{number:.5f}"

    if number >= 0.01:
        return f"{number:.6f}"

    return f"{number:.10f}"


def ema(series, span):
    return series.ewm(
        span=span,
        adjust=False,
    ).mean()


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(
        delta > 0,
        0.0,
    )
    loss = -delta.where(
        delta < 0,
        0.0,
    )

    average_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    average_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    rs = average_gain / average_loss.replace(
        0,
        0.0000001,
    )

    return 100 - (
        100 / (1 + rs)
    )


def calc_atr(frame, period=14):
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (
                high
                - previous_close
            ).abs(),
            (
                low
                - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()


def calc_adx(frame, period=14):
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where(
        (
            plus_dm > minus_dm
        )
        & (
            plus_dm > 0
        ),
        0.0,
    )

    minus_dm = minus_dm.where(
        (
            minus_dm > plus_dm
        )
        & (
            minus_dm > 0
        ),
        0.0,
    )

    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (
                high
                - previous_close
            ).abs(),
            (
                low
                - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    plus_di = (
        100
        * plus_dm.ewm(
            alpha=1 / period,
            adjust=False,
        ).mean()
        / atr.replace(
            0,
            0.0000001,
        )
    )

    minus_di = (
        100
        * minus_dm.ewm(
            alpha=1 / period,
            adjust=False,
        ).mean()
        / atr.replace(
            0,
            0.0000001,
        )
    )

    dx = (
        abs(plus_di - minus_di)
        / (
            plus_di + minus_di
        ).replace(
            0,
            0.0000001,
        )
        * 100
    )

    return dx.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()


def add_indicators(frame):
    if frame is None or frame.empty:
        return None

    data = frame.copy()

    data["ema20"] = ema(
        data["close"],
        20,
    )
    data["ema50"] = ema(
        data["close"],
        50,
    )
    data["ema200"] = ema(
        data["close"],
        200,
    )
    data["rsi"] = calc_rsi(
        data["close"]
    )
    data["atr"] = calc_atr(data)
    data["adx"] = calc_adx(data)
    data["volume_avg"] = (
        data["volume"]
        .rolling(20)
        .mean()
    )
    data["volume_ratio"] = (
        data["volume"]
        / data["volume_avg"].replace(
            0,
            0.0000001,
        )
    )

    data = (
        data
        .dropna()
        .reset_index(drop=True)
    )

    return (
        data
        if len(data) >= 20
        else None
    )


def pct(value, reference):
    try:
        if reference == 0:
            return 0.0

        return (
            (
                value - reference
            )
            / reference
            * 100
        )

    except Exception:
        return 0.0


def abs_pct(value, reference):
    return abs(
        pct(value, reference)
    )


def candle_is_green(row):
    return (
        safe_float(row["close"])
        > safe_float(row["open"])
    )


def candle_is_red(row):
    return (
        safe_float(row["close"])
        < safe_float(row["open"])
    )


def candle_body_percent(row):
    open_price = safe_float(row["open"])
    close_price = safe_float(row["close"])

    if open_price <= 0:
        return 0.0

    return (
        abs(close_price - open_price)
        / open_price
        * 100
    )


def rolling_support(frame, lookback=80):
    try:
        return float(
            frame["low"]
            .iloc[-lookback:-2]
            .min()
        )
    except Exception:
        return float(
            frame["low"]
            .iloc[-20:-2]
            .min()
        )


def rolling_resistance(
    frame,
    lookback=80,
):
    try:
        return float(
            frame["high"]
            .iloc[-lookback:-2]
            .max()
        )
    except Exception:
        return float(
            frame["high"]
            .iloc[-20:-2]
            .max()
        )


def clamp(value, minimum, maximum):
    return max(
        minimum,
        min(maximum, value),
    )


def build_condition(label, ok):
    return {
        "label": label,
        "ok": bool(ok),
    }


def missing_reasons(conditions):
    return [
        condition["label"]
        for condition in conditions
        if not condition["ok"]
    ]


def entry_zone_distance_percent(
    current_price,
    entry_low,
    entry_high,
):
    current_price = safe_float(
        current_price
    )
    entry_low = safe_float(
        entry_low
    )
    entry_high = safe_float(
        entry_high
    )

    if (
        current_price <= 0
        or entry_low <= 0
        or entry_high <= 0
    ):
        return 999.0

    low = min(
        entry_low,
        entry_high,
    )
    high = max(
        entry_low,
        entry_high,
    )

    if low <= current_price <= high:
        return 0.0

    nearest = (
        low
        if current_price < low
        else high
    )

    return abs_pct(
        current_price,
        nearest,
    )


def calculate_open_r(
    direction,
    entry,
    sl,
    current_price,
):
    entry = safe_float(entry)
    sl = safe_float(sl)
    current = safe_float(current_price)

    risk = abs(entry - sl)

    if (
        entry <= 0
        or sl <= 0
        or current <= 0
        or risk <= 0
    ):
        return None

    if str(direction).upper() == "LONG":
        result = (
            current - entry
        ) / risk

    elif str(direction).upper() == "SHORT":
        result = (
            entry - current
        ) / risk

    else:
        return None

    return round(result, 4)


def validate_direction_before_send(
    signal,
    current_price,
):
    try:
        direction = str(
            signal.get("direction", "")
        ).upper()

        timing_mode = str(
            signal.get(
                "timing_mode",
                "1H_ONAYLI",
            )
        )

        current = safe_float(
            current_price
        )
        h1_ema50 = safe_float(
            signal.get(
                "h1_ema50_reference"
            )
        )
        m15_ema20 = safe_float(
            signal.get(
                "m15_ema20_reference"
            )
        )

        if (
            current <= 0
            or h1_ema50 <= 0
        ):
            return (
                False,
                "yön referansı eksik",
            )

        normal_tolerance = (
            FINAL_NORMAL_DIRECTION_TOLERANCE_PERCENT
            / 100
        )
        early_tolerance = (
            FINAL_EARLY_DIRECTION_TOLERANCE_PERCENT
            / 100
        )

        if direction == "LONG":
            minimum_h1_price = (
                h1_ema50
                * (1 - normal_tolerance)
            )

            if current < minimum_h1_price:
                return (
                    False,
                    "1H yön yapısı LONG tarafında bozuldu",
                )

            if timing_mode == "15M_ERKEN":
                if m15_ema20 <= 0:
                    return (
                        False,
                        "15M erken yön referansı eksik",
                    )

                minimum_early_price = (
                    m15_ema20
                    * (1 - early_tolerance)
                )

                if current < minimum_early_price:
                    return (
                        False,
                        "15M erken LONG yapısı bozuldu",
                    )

        elif direction == "SHORT":
            maximum_h1_price = (
                h1_ema50
                * (1 + normal_tolerance)
            )

            if current > maximum_h1_price:
                return (
                    False,
                    "1H yön yapısı SHORT tarafında bozuldu",
                )

            if timing_mode == "15M_ERKEN":
                if m15_ema20 <= 0:
                    return (
                        False,
                        "15M erken yön referansı eksik",
                    )

                maximum_early_price = (
                    m15_ema20
                    * (1 + early_tolerance)
                )

                if current > maximum_early_price:
                    return (
                        False,
                        "15M erken SHORT yapısı bozuldu",
                    )

        else:
            return (
                False,
                "sinyal yönü geçersiz",
            )

        return True, "yön yapısı geçerli"

    except Exception as exc:
        return (
            False,
            f"son yön kontrol hatası: {exc}",
        )


def leverage_text(risk_percent):
    risk = safe_float(
        risk_percent
    )

    if risk <= 1.50:
        return "1x-2x"

    if risk <= 3.00:
        return "1x"

    return "Pas geç"


# =========================================================
# SKOR / MESAJ
# =========================================================

def calculate_quality_score(
    direction,
    risk_percent,
    rsi_1h,
    adx_4h,
    adx_1h,
    vol_4h,
    vol_1h,
    dist_1h_ema20,
    dist_4h_ema20,
    timing_mode,
    rsi_15m,
    vol_15m,
    dist_15m_ema20,
):
    score = 70.0

    # ADX: en fazla 9 puan.
    score += clamp(
        (
            adx_4h
            - MIN_ADX_4H
        )
        * 0.45,
        0,
        4.5,
    )
    score += clamp(
        (
            adx_1h
            - MIN_ADX_1H
        )
        * 0.45,
        0,
        4.5,
    )

    # Hacim: en fazla 8 puan.
    score += clamp(
        (
            vol_4h
            - MIN_VOLUME_RATIO
        )
        * 4.0,
        0,
        4.0,
    )
    score += clamp(
        (
            vol_1h
            - MIN_VOLUME_RATIO
        )
        * 4.0,
        0,
        4.0,
    )

    # 1H RSI kalitesi.
    if direction == "LONG":
        rsi_quality = (
            1.0
            - min(
                abs(
                    rsi_1h - 55.0
                )
                / 18.0,
                1.0,
            )
        )
    else:
        rsi_quality = (
            1.0
            - min(
                abs(
                    rsi_1h - 45.0
                )
                / 18.0,
                1.0,
            )
        )

    score += rsi_quality * 5.0

    # Dusuk stop: en fazla 6 puan.
    risk_quality = (
        MAX_RISK_PERCENT
        - risk_percent
    ) / max(
        0.0001,
        MAX_RISK_PERCENT
        - MIN_RISK_PERCENT,
    )

    score += clamp(
        risk_quality,
        0,
        1,
    ) * 6.0

    # EMA yakinligi: en fazla 2 puan.
    distance_quality = (
        1.0
        - min(
            (
                dist_1h_ema20
                / MAX_DISTANCE_FROM_1H_EMA20_PERCENT
                + dist_4h_ema20
                / MAX_DISTANCE_FROM_4H_EMA20_PERCENT
            )
            / 2.0,
            1.0,
        )
    )

    score += (
        distance_quality
        * 2.0
    )

    # Erken 15M yolunda ek kalite.
    if timing_mode == "15M_ERKEN":
        if (
            vol_15m >= 1.0
        ):
            score += 1.5

        if (
            dist_15m_ema20
            <= 0.55
        ):
            score += 1.5

        if direction == "LONG":
            if 48 <= rsi_15m <= 62:
                score += 1.0
        else:
            if 38 <= rsi_15m <= 52:
                score += 1.0

    return int(
        round(
            clamp(
                score,
                0,
                99,
            )
        )
    )


def build_signal_message(signal):
    icon = (
        "🟢"
        if signal["direction"] == "LONG"
        else "🔴"
    )

    if signal["score"] >= 90:
        quality = "A+ Swing"
    elif signal["score"] >= 85:
        quality = "A Swing"
    else:
        quality = "B+ Dikkatli Swing"

    timing_text = (
        "15M ilk red ile erken giriş"
        if signal["timing_mode"]
        == "15M_ERKEN"
        else "Kapanmış 1H mum onayı"
    )

    return (
        f"📈 {BOT_NAME}\n\n"
        f"{icon} {signal['direction']}\n"
        f"🟡 Coin: {signal['symbol']}\n"
        f"⏱️ Kaynak: {signal['source']}\n"
        f"⚡ Zamanlama: {timing_text}\n"
        f"📌 Kurulum: {signal['setup']}\n\n"
        f"📌 Giriş: "
        f"{format_price(signal['entry'])}\n"
        f"📍 Giriş Bölgesi: "
        f"{format_price(signal['entry_low'])}"
        f" - "
        f"{format_price(signal['entry_high'])}\n"
        f"🎯 TP1: "
        f"{format_price(signal['tp1'])}\n"
        f"🎯 TP2: "
        f"{format_price(signal['tp2'])}\n"
        f"🎯 TP3: "
        f"{format_price(signal['tp3'])}\n"
        f"🛑 SL: "
        f"{format_price(signal['sl'])}\n\n"
        f"📊 Kalite Uyum Skoru: "
        f"{signal['score']}/100 ({quality})\n"
        f"🛡️ Stop Mesafesi: "
        f"%{round(signal['risk_percent'], 2)}\n"
        f"⚙️ Kaldıraç Önerisi: "
        f"{leverage_text(signal['risk_percent'])}\n\n"
        f"🧭 Çoklu Zaman Dilimi:\n"
        f"• 1D: {signal['d1_note']}\n"
        f"• 4H: {signal['h4_note']}\n"
        f"• 1H: {signal['h1_note']}\n"
        f"• 15M: {signal['m15_note']}\n\n"
        f"📊 Göstergeler:\n"
        f"• 1D RSI: "
        f"{round(signal['rsi_d1'], 2)}\n"
        f"• 4H RSI: "
        f"{round(signal['rsi_4h'], 2)}\n"
        f"• 1H RSI: "
        f"{round(signal['rsi_1h'], 2)}\n"
        f"• 15M RSI: "
        f"{round(signal['rsi_15m'], 2)}\n"
        f"• 4H ADX: "
        f"{round(signal['adx_4h'], 2)}\n"
        f"• 1H ADX: "
        f"{round(signal['adx_1h'], 2)}\n"
        f"• 1H Hacim: "
        f"{round(signal['vol_1h'], 2)}x\n"
        f"• 4H Hacim: "
        f"{round(signal['vol_4h'], 2)}x\n"
        f"• 15M Hacim: "
        f"{round(signal['vol_15m'], 2)}x\n"
        f"• Destek: "
        f"{format_price(signal['support'])}\n"
        f"• Direnç: "
        f"{format_price(signal['resistance'])}\n\n"
        f"📌 İşlem Kuralı:\n"
        f"• Swing sinyalidir; scalp gibi hızlı işlem değildir.\n"
        f"• Giriş bölgesinden uzaklaştıysa işleme girme.\n"
        f"• TP1 gelirse %50 kâr al, SL girişe çek.\n"
        f"• Stop mutlaka girilmeli.\n"
        f"• Marjin: Isolated.\n"
        f"• Kaldıraç düşük tutulmalı.\n\n"
        f"⚠️ Finansal tavsiye değildir. "
        f"Grafikte kontrol etmeden işlem açma."
    )


# =========================================================
# SWING ANALIZI
# =========================================================

def analyze_direction(
    symbol,
    direction,
    df1d,
    df4h,
    df1h,
    df15m,
    current_price,
):
    try:
        d1 = add_indicators(df1d)
        h4 = add_indicators(df4h)
        h1 = add_indicators(df1h)
        m15 = add_indicators(df15m)

        if (
            d1 is None
            or h4 is None
            or h1 is None
            or m15 is None
        ):
            return None, None

        if (
            len(d1) < 220
            or len(h4) < 220
            or len(h1) < 220
            or len(m15) < 120
        ):
            return None, None

        last_d1 = d1.iloc[-2]
        last_h4 = h4.iloc[-2]
        last_h1 = h1.iloc[-2]
        prev_h1 = h1.iloc[-3]
        forming_h1 = h1.iloc[-1]

        last_m15 = m15.iloc[-2]
        prev_m15 = m15.iloc[-3]
        forming_m15 = m15.iloc[-1]

        entry = (
            safe_float(current_price)
            if safe_float(current_price) > 0
            else safe_float(last_m15["close"])
        )

        if entry <= 0:
            return None, None

        atr_4h = safe_float(last_h4["atr"])
        atr_1h = safe_float(last_h1["atr"])
        atr_15m = safe_float(last_m15["atr"])

        if (
            atr_4h <= 0
            or atr_1h <= 0
            or atr_15m <= 0
        ):
            return None, None

        support = rolling_support(
            h4,
            80,
        )
        resistance = rolling_resistance(
            h4,
            80,
        )

        d_close = safe_float(
            last_d1["close"]
        )
        d_ema20 = safe_float(
            last_d1["ema20"]
        )
        d_ema50 = safe_float(
            last_d1["ema50"]
        )
        d_ema200 = safe_float(
            last_d1["ema200"]
        )

        h4_close = safe_float(
            last_h4["close"]
        )
        h4_ema20 = safe_float(
            last_h4["ema20"]
        )
        h4_ema50 = safe_float(
            last_h4["ema50"]
        )
        h4_ema200 = safe_float(
            last_h4["ema200"]
        )

        h1_close = safe_float(
            last_h1["close"]
        )
        h1_ema20 = safe_float(
            last_h1["ema20"]
        )
        h1_ema50 = safe_float(
            last_h1["ema50"]
        )

        forming_h1_close = safe_float(
            forming_h1["close"]
        )
        forming_h1_ema20 = safe_float(
            forming_h1["ema20"]
        )
        forming_h1_ema50 = safe_float(
            forming_h1["ema50"]
        )

        m15_close = safe_float(
            last_m15["close"]
        )
        m15_ema20 = safe_float(
            last_m15["ema20"]
        )
        m15_ema50 = safe_float(
            last_m15["ema50"]
        )

        rsi_d1 = safe_float(
            last_d1["rsi"]
        )
        rsi_4h = safe_float(
            last_h4["rsi"]
        )
        rsi_1h = safe_float(
            last_h1["rsi"]
        )
        rsi_15m = safe_float(
            last_m15["rsi"]
        )

        adx_4h = safe_float(
            last_h4["adx"]
        )
        adx_1h = safe_float(
            last_h1["adx"]
        )

        vol_4h = safe_float(
            last_h4["volume_ratio"]
        )
        vol_1h = safe_float(
            last_h1["volume_ratio"]
        )
        vol_15m = safe_float(
            last_m15["volume_ratio"]
        )

        dist_1h_ema20 = abs_pct(
            entry,
            h1_ema20,
        )
        dist_4h_ema20 = abs_pct(
            entry,
            h4_ema20,
        )
        dist_15m_ema20 = abs_pct(
            entry,
            m15_ema20,
        )

        if direction == "LONG":
            d1_trend = (
                d_close > d_ema50
                and d_ema20 >= d_ema50
            )
            d1_safe = (
                d_close > d_ema200
                or d_ema50 > d_ema200
            )

            h4_trend = (
                h4_close > h4_ema50
                and h4_ema20 >= h4_ema50
            )
            h4_safe = (
                h4_close > h4_ema200
                or h4_ema50 >= h4_ema200
            )

            h1_confirm = (
                h1_close > h1_ema20
                or (
                    candle_is_green(
                        last_h1
                    )
                    and h1_close > h1_ema50
                )
            )
            h1_turn = (
                candle_is_green(last_h1)
                or h1_close
                > safe_float(
                    prev_h1["close"]
                )
            )

            # Erken yolda kapanmamis 1H yapisi kullanilir;
            # ancak tek basina yeterli degildir.
            h1_early_structure = (
                forming_h1_close
                > forming_h1_ema50
                and (
                    forming_h1_close
                    >= forming_h1_ema20
                    or candle_is_green(
                        forming_h1
                    )
                )
                and h1_close
                >= h1_ema50 * 0.992
            )

            m15_confirm = (
                m15_close > m15_ema20
                or (
                    candle_is_green(
                        last_m15
                    )
                    and m15_close > m15_ema50
                )
            )

            m15_turn = (
                candle_is_green(
                    last_m15
                )
                and m15_close
                > safe_float(
                    prev_m15["close"]
                )
            )

            m15_first_trigger = (
                m15_close
                >= safe_float(
                    prev_m15["high"]
                )
                or (
                    safe_float(
                        last_m15["low"]
                    )
                    <= m15_ema20 * 1.003
                    and m15_close > m15_ema20
                    and candle_is_green(
                        last_m15
                    )
                )
            )

            m15_rsi_ok = (
                44 <= rsi_15m <= 68
            )

            rsi_ok = (
                42 <= rsi_1h <= 68
                and rsi_4h <= 72
                and rsi_d1 <= 74
            )

            atr_stop = (
                entry
                - atr_4h * 1.15
            )
            support_stop = (
                support * 0.995
            )
            sl = max(
                min(
                    atr_stop,
                    entry * 0.992,
                ),
                support_stop,
            )

            if sl >= entry:
                sl = (
                    entry
                    - atr_4h * 1.10
                )

            risk = entry - sl
            risk_percent = (
                risk / entry * 100
            )

            tp1 = (
                entry + risk * TP1_R
            )
            tp2 = (
                entry + risk * TP2_R
            )
            tp3 = (
                entry + risk * TP3_R
            )

            d1_note = (
                "1D trend yukarı"
                if d1_trend
                else "1D trend zayıf"
            )
            h4_note = (
                "4H trend yukarı"
                if h4_trend
                else "4H trend zayıf/karışık"
            )
            normal_h1_note = (
                "1H kapanmış alış onayı"
                if h1_confirm
                else "1H onay zayıf"
            )
            early_h1_note = (
                "1H yapı yukarı hazırlanıyor"
                if h1_early_structure
                else "1H erken yapı yok"
            )
            m15_note = (
                "15M ilk alış dönüşü"
                if (
                    m15_confirm
                    and m15_turn
                    and m15_first_trigger
                )
                else "15M erken dönüş eksik"
            )

            normal_setup = (
                "1D + 4H trend uyumlu "
                "1H Onaylı Swing LONG"
            )
            early_setup = (
                "1D + 4H trend uyumlu "
                "15M Erken Swing LONG"
            )

            invalidated_normal = (
                safe_float(
                    forming_h1["low"]
                )
                <= sl
            )
            invalidated_early = (
                safe_float(
                    forming_m15["low"]
                )
                <= sl
            )

        else:
            d1_trend = (
                d_close < d_ema50
                and d_ema20 <= d_ema50
            )
            d1_safe = (
                d_close < d_ema200
                or d_ema50 < d_ema200
            )

            h4_trend = (
                h4_close < h4_ema50
                and h4_ema20 <= h4_ema50
            )
            h4_safe = (
                h4_close < h4_ema200
                or h4_ema50 <= h4_ema200
            )

            h1_confirm = (
                h1_close < h1_ema20
                or (
                    candle_is_red(
                        last_h1
                    )
                    and h1_close < h1_ema50
                )
            )
            h1_turn = (
                candle_is_red(last_h1)
                or h1_close
                < safe_float(
                    prev_h1["close"]
                )
            )

            h1_early_structure = (
                forming_h1_close
                < forming_h1_ema50
                and (
                    forming_h1_close
                    <= forming_h1_ema20
                    or candle_is_red(
                        forming_h1
                    )
                )
                and h1_close
                <= h1_ema50 * 1.008
            )

            m15_confirm = (
                m15_close < m15_ema20
                or (
                    candle_is_red(
                        last_m15
                    )
                    and m15_close < m15_ema50
                )
            )

            m15_turn = (
                candle_is_red(
                    last_m15
                )
                and m15_close
                < safe_float(
                    prev_m15["close"]
                )
            )

            m15_first_trigger = (
                m15_close
                <= safe_float(
                    prev_m15["low"]
                )
                or (
                    safe_float(
                        last_m15["high"]
                    )
                    >= m15_ema20 * 0.997
                    and m15_close < m15_ema20
                    and candle_is_red(
                        last_m15
                    )
                )
            )

            m15_rsi_ok = (
                32 <= rsi_15m <= 56
            )

            rsi_ok = (
                32 <= rsi_1h <= 58
                and rsi_4h >= 25
                and rsi_d1 >= 22
            )

            atr_stop = (
                entry
                + atr_4h * 1.15
            )
            resistance_stop = (
                resistance * 1.005
            )

            sl = min(
                max(
                    atr_stop,
                    entry * 1.008,
                ),
                resistance_stop,
            )

            if sl <= entry:
                sl = (
                    entry
                    + atr_4h * 1.10
                )

            risk = sl - entry
            risk_percent = (
                risk / entry * 100
            )

            tp1 = (
                entry - risk * TP1_R
            )
            tp2 = (
                entry - risk * TP2_R
            )
            tp3 = (
                entry - risk * TP3_R
            )

            d1_note = (
                "1D trend aşağı"
                if d1_trend
                else "1D trend zayıf"
            )
            h4_note = (
                "4H trend aşağı"
                if h4_trend
                else "4H trend zayıf/karışık"
            )
            normal_h1_note = (
                "1H kapanmış satış onayı"
                if h1_confirm
                else "1H onay zayıf"
            )
            early_h1_note = (
                "1H yapı aşağı hazırlanıyor"
                if h1_early_structure
                else "1H erken yapı yok"
            )
            m15_note = (
                "15M ilk satış reddi"
                if (
                    m15_confirm
                    and m15_turn
                    and m15_first_trigger
                )
                else "15M erken satış reddi eksik"
            )

            normal_setup = (
                "1D + 4H trend uyumlu "
                "1H Onaylı Swing SHORT"
            )
            early_setup = (
                "1D + 4H trend uyumlu "
                "15M Erken Swing SHORT"
            )

            invalidated_normal = (
                safe_float(
                    forming_h1["high"]
                )
                >= sl
            )
            invalidated_early = (
                safe_float(
                    forming_m15["high"]
                )
                >= sl
            )

        adx_ok = (
            adx_4h >= MIN_ADX_4H
            or adx_1h >= MIN_ADX_1H
        )

        volume_ok = (
            vol_1h >= MIN_VOLUME_RATIO
            or vol_4h >= MIN_VOLUME_RATIO
        )

        not_extended = (
            dist_1h_ema20
            <= MAX_DISTANCE_FROM_1H_EMA20_PERCENT
            and dist_4h_ema20
            <= MAX_DISTANCE_FROM_4H_EMA20_PERCENT
        )

        risk_ok = (
            MIN_RISK_PERCENT
            <= risk_percent
            <= MAX_RISK_PERCENT
        )

        normal_path = (
            h1_confirm
            and h1_turn
        )

        early_volume_ok = (
            vol_15m
            >= MIN_EARLY_15M_VOLUME_RATIO
            and (
                vol_1h >= 1.0
                or vol_4h >= 1.0
                or vol_15m >= 1.20
            )
        )

        early_not_extended = (
            dist_15m_ema20
            <= MAX_EARLY_DISTANCE_FROM_15M_EMA20_PERCENT
        )

        early_body_ok = (
            candle_body_percent(
                last_m15
            )
            >= 0.04
        )

        early_path = (
            not normal_path
            and h1_early_structure
            and m15_confirm
            and m15_turn
            and m15_first_trigger
            and m15_rsi_ok
            and early_volume_ok
            and early_not_extended
            and early_body_ok
        )

        if normal_path:
            timing_mode = "1H_ONAYLI"
            setup = normal_setup
            h1_note = normal_h1_note
            source = "SWING_RADAR"
            invalidated_before_send = (
                invalidated_normal
            )

            entry_low = (
                entry - atr_1h * 0.35
                if direction == "LONG"
                else entry - atr_1h * 0.25
            )
            entry_high = (
                entry + atr_1h * 0.25
                if direction == "LONG"
                else entry + atr_1h * 0.35
            )

        elif early_path:
            timing_mode = "15M_ERKEN"
            setup = early_setup
            h1_note = early_h1_note
            source = "SWING_EARLY_15M"
            invalidated_before_send = (
                invalidated_early
            )

            # Erken yolun giris bolgesi daha dardir.
            entry_low = (
                entry - atr_15m * 0.30
                if direction == "LONG"
                else entry - atr_15m * 0.18
            )
            entry_high = (
                entry + atr_15m * 0.18
                if direction == "LONG"
                else entry + atr_15m * 0.30
            )

        else:
            timing_mode = "BEKLE"
            setup = normal_setup
            h1_note = normal_h1_note
            source = "SWING_RADAR"
            invalidated_before_send = False
            entry_low = entry
            entry_high = entry

        common_conditions = [
            build_condition(
                "1D trend uyumlu değil",
                d1_trend,
            ),
            build_condition(
                "1D ema200 güvenli değil",
                d1_safe,
            ),
            build_condition(
                "4H trend uyumlu değil",
                h4_trend,
            ),
            build_condition(
                "4H ana yapı zayıf",
                h4_safe,
            ),
            build_condition(
                "RSI swing için uygun değil",
                rsi_ok,
            ),
            build_condition(
                "ADX trend gücü düşük",
                adx_ok,
            ),
            build_condition(
                "hacim onayı düşük",
                volume_ok,
            ),
            build_condition(
                "fiyat EMA'lara göre çok uzak",
                not_extended,
            ),
            build_condition(
                "risk uygun değil",
                risk_ok,
            ),
        ]

        if timing_mode == "1H_ONAYLI":
            timing_conditions = [
                build_condition(
                    "1H giriş onayı yok",
                    h1_confirm,
                ),
                build_condition(
                    "1H dönüş mumu yok",
                    h1_turn,
                ),
                build_condition(
                    "kurulum sinyalden önce stop alanını gördü",
                    not invalidated_before_send,
                ),
            ]

        elif timing_mode == "15M_ERKEN":
            timing_conditions = [
                build_condition(
                    "1H erken yapı hazır değil",
                    h1_early_structure,
                ),
                build_condition(
                    "15M trend onayı yok",
                    m15_confirm,
                ),
                build_condition(
                    "15M dönüş mumu yok",
                    m15_turn,
                ),
                build_condition(
                    "15M ilk kırılım/red yok",
                    m15_first_trigger,
                ),
                build_condition(
                    "15M RSI erken girişe uygun değil",
                    m15_rsi_ok,
                ),
                build_condition(
                    "15M erken hacim yetersiz",
                    early_volume_ok,
                ),
                build_condition(
                    "15M giriş EMA20'den uzak",
                    early_not_extended,
                ),
                build_condition(
                    "15M mum gövdesi zayıf",
                    early_body_ok,
                ),
                build_condition(
                    "kurulum sinyalden önce stop alanını gördü",
                    not invalidated_before_send,
                ),
            ]

        else:
            timing_conditions = [
                build_condition(
                    "1H veya 15M giriş zamanlaması yok",
                    False,
                ),
            ]

        conditions = (
            common_conditions
            + timing_conditions
        )

        ok_count = sum(
            1
            for condition in conditions
            if condition["ok"]
        )
        total_conditions = len(
            conditions
        )

        hard_ok = all(
            condition["ok"]
            for condition in conditions
        )

        score = calculate_quality_score(
            direction=direction,
            risk_percent=risk_percent,
            rsi_1h=rsi_1h,
            adx_4h=adx_4h,
            adx_1h=adx_1h,
            vol_4h=vol_4h,
            vol_1h=vol_1h,
            dist_1h_ema20=dist_1h_ema20,
            dist_4h_ema20=dist_4h_ema20,
            timing_mode=timing_mode,
            rsi_15m=rsi_15m,
            vol_15m=vol_15m,
            dist_15m_ema20=dist_15m_ema20,
        )

        minimum_score = (
            MIN_SCORE_EARLY
            if timing_mode == "15M_ERKEN"
            else MIN_SCORE_NORMAL
        )

        debug = {
            "symbol": symbol,
            "direction": direction,
            "timing_mode": timing_mode,
            "score": score,
            "minimum_score": minimum_score,
            "ok_count": ok_count,
            "total_conditions": total_conditions,
            "missing": missing_reasons(
                conditions
            ),
            "risk_percent": risk_percent,
            "rsi_1h": rsi_1h,
            "rsi_15m": rsi_15m,
            "adx_4h": adx_4h,
            "adx_1h": adx_1h,
            "vol_1h": vol_1h,
            "vol_4h": vol_4h,
            "vol_15m": vol_15m,
            "dist_1h_ema20": dist_1h_ema20,
            "dist_4h_ema20": dist_4h_ema20,
            "dist_15m_ema20": dist_15m_ema20,
        }

        if (
            not hard_ok
            or score < minimum_score
        ):
            return None, debug

        signal = {
            "symbol": normalize_bot_symbol(
                symbol
            ),
            "direction": direction,
            "source": source,
            "timing_mode": timing_mode,
            "setup": setup,
            "entry": entry,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "sl": sl,
            "score": score,
            "minimum_score": minimum_score,
            "risk_percent": risk_percent,
            "max_zone_drift": (
                MAX_EARLY_ENTRY_ZONE_DRIFT_PERCENT
                if timing_mode
                == "15M_ERKEN"
                else MAX_ENTRY_ZONE_DRIFT_PERCENT
            ),
            "d1_note": d1_note,
            "h4_note": h4_note,
            "h1_note": h1_note,
            "m15_note": m15_note,
            "rsi_d1": rsi_d1,
            "rsi_4h": rsi_4h,
            "rsi_1h": rsi_1h,
            "rsi_15m": rsi_15m,
            "adx_4h": adx_4h,
            "adx_1h": adx_1h,
            "vol_4h": vol_4h,
            "vol_1h": vol_1h,
            "vol_15m": vol_15m,
            "dist_1h_ema20": dist_1h_ema20,
            "dist_4h_ema20": dist_4h_ema20,
            "dist_15m_ema20": dist_15m_ema20,
            "support": support,
            "resistance": resistance,
            "h1_ema50_reference": h1_ema50,
            "m15_ema20_reference": m15_ema20,
            "m15_ema50_reference": m15_ema50,
            "ok_count": ok_count,
            "total_conditions": total_conditions,
            "missing": [],
        }

        signal["message"] = (
            build_signal_message(signal)
        )

        return signal, debug

    except Exception as exc:
        print(
            symbol,
            direction,
            "swing analiz hatası:",
            exc,
        )
        return None, None


def analyze_symbol(exchange, symbol):
    current_price = get_current_price(
        exchange,
        symbol,
    )

    df1d = fetch_df(
        exchange,
        symbol,
        "1d",
        limit=D1_LIMIT,
        min_len=220,
    )

    df4h = fetch_df(
        exchange,
        symbol,
        "4h",
        limit=H4_LIMIT,
        min_len=220,
    )

    df1h = fetch_df(
        exchange,
        symbol,
        "1h",
        limit=H1_LIMIT,
        min_len=220,
    )

    df15m = fetch_df(
        exchange,
        symbol,
        "15m",
        limit=M15_LIMIT,
        min_len=120,
    )

    long_signal, long_debug = (
        analyze_direction(
            symbol,
            "LONG",
            df1d,
            df4h,
            df1h,
            df15m,
            current_price,
        )
    )

    short_signal, short_debug = (
        analyze_direction(
            symbol,
            "SHORT",
            df1d,
            df4h,
            df1h,
            df15m,
            current_price,
        )
    )

    signals = []

    if long_signal is not None:
        signals.append(long_signal)

    if short_signal is not None:
        signals.append(short_signal)

    return (
        signals,
        long_debug,
        short_debug,
    )


# =========================================================
# TEKRAR / ACIK SINYAL
# =========================================================

def duplicate_key(symbol, direction):
    return (
        f"{normalize_bot_symbol(symbol)}_"
        f"{direction}"
    )


def is_recent_duplicate(
    state,
    symbol,
    direction,
):
    last_time = int(
        state.get(
            "last_sent",
            {},
        ).get(
            duplicate_key(
                symbol,
                direction,
            ),
            0,
        )
    )

    return (
        now_ts() - last_time
        < DUPLICATE_SECONDS
    )


def mark_sent(
    state,
    symbol,
    direction,
):
    state.setdefault(
        "last_sent",
        {},
    )

    state["last_sent"][
        duplicate_key(
            symbol,
            direction,
        )
    ] = now_ts()

    cutoff = (
        now_ts()
        - 7 * 24 * 60 * 60
    )

    state["last_sent"] = {
        key: value
        for key, value
        in state["last_sent"].items()
        if int(value) >= cutoff
    }

    save_state(state)


def has_open_same_symbol(
    state,
    symbol,
):
    symbol = normalize_bot_symbol(
        symbol
    )

    return any(
        normalize_bot_symbol(
            signal.get("symbol")
        )
        == symbol
        for signal in state.get(
            "open_swing_signals",
            {},
        ).values()
    )


def save_open_signal(
    state,
    signal,
):
    key = (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal['source']}"
    )

    opened_at = now_ts()

    state.setdefault(
        "open_swing_signals",
        {},
    )

    state["open_swing_signals"][
        key
    ] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "source": signal["source"],
        "timing_mode": signal.get(
            "timing_mode",
            "1H_ONAYLI",
        ),
        "entry": signal["entry"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "tp3": signal["tp3"],
        "sl": signal["sl"],
        "score": signal["score"],
        "risk_percent": signal[
            "risk_percent"
        ],
        "performance_record_id": signal.get(
            "performance_record_id"
        ),
        "setup": signal.get("setup"),
        "entry_low": signal.get("entry_low"),
        "entry_high": signal.get("entry_high"),
        "zone_drift": signal.get("zone_drift"),
        "direction_check": signal.get(
            "direction_check"
        ),
        "portfolio_risk": signal.get(
            "portfolio_risk"
        ),
        "rsi_d1": signal.get("rsi_d1"),
        "rsi_4h": signal.get("rsi_4h"),
        "rsi_1h": signal.get("rsi_1h"),
        "rsi_15m": signal.get("rsi_15m"),
        "adx_4h": signal.get("adx_4h"),
        "adx_1h": signal.get("adx_1h"),
        "vol_4h": signal.get("vol_4h"),
        "vol_1h": signal.get("vol_1h"),
        "vol_15m": signal.get("vol_15m"),
        "dist_1h_ema20": signal.get(
            "dist_1h_ema20"
        ),
        "dist_4h_ema20": signal.get(
            "dist_4h_ema20"
        ),
        "dist_15m_ema20": signal.get(
            "dist_15m_ema20"
        ),
        "d1_note": signal.get("d1_note"),
        "h4_note": signal.get("h4_note"),
        "h1_note": signal.get("h1_note"),
        "m15_note": signal.get("m15_note"),
        "ok_count": signal.get("ok_count"),
        "total_conditions": signal.get(
            "total_conditions"
        ),
        "missing": list(signal.get("missing") or []),
        "best_favorable_percent": 0.0,
        "worst_adverse_percent": 0.0,
        "best_favorable_r": 0.0,
        "worst_adverse_r": 0.0,
        "best_favorable_price": signal.get("entry"),
        "worst_adverse_price": signal.get("entry"),
        "last_market_price": signal.get(
            "current_price",
            signal.get("entry"),
        ),
        "opened_at": opened_at,
        "last_checked_at": opened_at,
        "tp1_hit": False,
        "tp1_hit_at": 0,
        "tp2_hit": False,
        "tp3_hit": False,
        "closed": False,
    }

    increment_stat(
        state,
        "signals",
    )

    if (
        signal.get("timing_mode")
        == "15M_ERKEN"
    ):
        increment_stat(
            state,
            "early_signals",
        )
    else:
        increment_stat(
            state,
            "normal_signals",
        )

    save_state(state)


# =========================================================
# BILDIRIMLER
# =========================================================

def notify_tp1(
    state,
    symbol,
    direction,
    entry,
    tp1,
):
    increment_stat(state, "tp1")

    update_swing_trade_outcome(
        symbol,
        direction,
        "TP1",
        current_price=tp1,
    )

    icon = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    send_telegram(
        f"✅ SWING TP1 GELDİ\n\n"
        f"{icon} {symbol} {direction}\n"
        f"Giriş: {format_price(entry)}\n"
        f"TP1: {format_price(tp1)}\n\n"
        f"Öneri: %50 kâr al, "
        f"SL giriş fiyatına çek."
    )


def notify_tp2(
    state,
    symbol,
    direction,
    tp2,
):
    increment_stat(state, "tp2")

    update_swing_trade_outcome(
        symbol,
        direction,
        "TP2",
        current_price=tp2,
    )

    send_telegram(
        f"✅ SWING TP2 GELDİ\n\n"
        f"{symbol} {direction}\n"
        f"TP2: {format_price(tp2)}"
    )


def notify_tp3(
    state,
    symbol,
    direction,
    tp3,
):
    increment_stat(state, "tp3")

    update_swing_trade_outcome(
        symbol,
        direction,
        "TP3",
        current_price=tp3,
    )

    send_telegram(
        f"🏁 SWING TP3 GELDİ\n\n"
        f"{symbol} {direction}\n"
        f"TP3: {format_price(tp3)}\n"
        f"Swing sinyali maksimum hedefe ulaştı."
    )


def notify_stop(
    state,
    symbol,
    direction,
    entry,
    sl,
    current,
):
    increment_stat(state, "stop")

    update_swing_trade_outcome(
        symbol,
        direction,
        "STOP",
        current_price=current,
    )

    send_telegram(
        f"❌ SWING STOP OLDU\n\n"
        f"{symbol} {direction}\n"
        f"Giriş: {format_price(entry)}\n"
        f"SL: {format_price(sl)}\n"
        f"Son fiyat: {format_price(current)}"
    )


def notify_breakeven(
    state,
    symbol,
    direction,
    entry,
):
    increment_stat(
        state,
        "breakeven",
    )

    update_swing_trade_outcome(
        symbol,
        direction,
        "BREAKEVEN",
        current_price=entry,
        result_r=0.0,
    )

    send_telegram(
        f"🟡 SWING KALAN İŞLEM "
        f"GİRİŞTEN KAPANDI\n\n"
        f"{symbol} {direction}\n"
        f"Giriş: {format_price(entry)}"
    )


def notify_expired(
    state,
    symbol,
    direction,
    entry,
    current_price,
    result_r,
    tp1_hit,
):
    increment_stat(
        state,
        "expired",
    )

    update_swing_trade_outcome(
        symbol,
        direction,
        "EXPIRED",
        current_price=current_price,
        result_r=result_r,
    )

    result_text = (
        f"{result_r:+.3f}R"
        if result_r is not None
        else "ölçülemedi"
    )

    tp1_text = (
        "Evet"
        if tp1_hit
        else "Hayır"
    )

    send_telegram(
        f"⏳ SWING SİNYAL SÜRESİ DOLDU\n\n"
        f"{symbol} {direction}\n"
        f"Giriş: {format_price(entry)}\n"
        f"Süre Sonu Fiyatı: "
        f"{format_price(current_price)}\n"
        f"Yaklaşık Açık Sonuç: "
        f"{result_text}\n"
        f"TP1 Daha Önce Geldi mi: "
        f"{tp1_text}\n\n"
        f"{MAX_OPEN_SIGNAL_HOURS} saat sonunda "
        f"takipten çıkarıldı."
    )


# =========================================================
# ACIK SWING TAKIBI
# =========================================================

def check_open_signals(
    exchange,
    state,
):
    open_signals = state.get(
        "open_swing_signals",
        {},
    )

    if not open_signals:
        print("Açık swing sinyali yok.")
        return

    updated = {}

    max_age = (
        MAX_OPEN_SIGNAL_HOURS
        * 60
        * 60
    )

    for key, signal in open_signals.items():
        try:
            symbol = normalize_bot_symbol(
                signal["symbol"]
            )
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
                bool(
                    signal.get(
                        "tp3_hit",
                        False,
                    )
                )
                or bool(
                    signal.get(
                        "closed",
                        False,
                    )
                )
            ):
                continue

            if (
                now_ts() - opened_at
                > max_age
            ):
                expiry_price = get_current_price(
                    exchange,
                    symbol,
                )

                # Güncel fiyat alınamazsa sonucu ölçmeden kapatma.
                # Sonraki çalışmada yeniden kontrol edilir.
                if expiry_price is None:
                    updated[key] = signal
                    print(
                        symbol,
                        "süre doldu fakat güncel fiyat alınamadı.",
                    )
                    continue

                expiry_r = calculate_open_r(
                    direction,
                    entry,
                    sl,
                    expiry_price,
                )

                sync_swing_open_metrics(signal)


                notify_expired(
                    state,
                    symbol,
                    direction,
                    entry,
                    expiry_price,
                    expiry_r,
                    bool(
                        signal.get(
                            "tp1_hit",
                            False,
                        )
                    ),
                )
                continue

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                since_seconds=max(
                    opened_at,
                    last_checked_at
                    - 30 * 60,
                ),
                limit=TRACK_LIMIT,
            )

            if not candles:
                updated[key] = signal
                continue

            tp1_hit = bool(
                signal.get(
                    "tp1_hit",
                    False,
                )
            )
            tp1_hit_at = int(
                signal.get(
                    "tp1_hit_at",
                    0,
                )
                or 0
            )
            tp2_hit = bool(
                signal.get(
                    "tp2_hit",
                    False,
                )
            )
            tp3_hit = bool(
                signal.get(
                    "tp3_hit",
                    False,
                )
            )

            closed = False

            for candle in candles:
                high = float(
                    candle["high"]
                )
                low = float(
                    candle["low"]
                )
                close = float(
                    candle["close"]
                )
                candle_time = int(
                    candle.get("time", 0)
                    or 0
                )

                update_swing_excursion(
                    signal,
                    high,
                    low,
                    candle_time=candle_time,
                )
                signal["last_market_price"] = close

                # TP1'in ilk geldigi mumda
                # BE kontrol edilmez.
                just_hit_tp1 = False

                if direction == "LONG":
                    if not tp1_hit:
                        if (
                            low <= sl
                            and high >= tp1
                        ):
                            if close >= entry:
                                tp1_hit = True
                                just_hit_tp1 = True
                                tp1_hit_at = (
                                    candle_time or now_ts()
                                )
                                signal["tp1_hit"] = True
                                signal["tp1_hit_at"] = tp1_hit_at

                                sync_swing_open_metrics(signal)


                                notify_tp1(
                                    state,
                                    symbol,
                                    direction,
                                    entry,
                                    tp1,
                                )
                            else:
                                sync_swing_open_metrics(signal)

                                add_swing_post_stop_follow(
                                    state,
                                    signal,
                                    close,
                                )

                                notify_stop(
                                    state,
                                    symbol,
                                    direction,
                                    entry,
                                    sl,
                                    close,
                                )
                                closed = True
                                break

                        elif low <= sl:
                            sync_swing_open_metrics(signal)

                            add_swing_post_stop_follow(
                                state,
                                signal,
                                close,
                            )

                            notify_stop(
                                state,
                                symbol,
                                direction,
                                entry,
                                sl,
                                close,
                            )
                            closed = True
                            break

                        elif high >= tp1:
                            tp1_hit = True
                            just_hit_tp1 = True
                            tp1_hit_at = (
                                candle_time or now_ts()
                            )
                            signal["tp1_hit"] = True
                            signal["tp1_hit_at"] = tp1_hit_at

                            sync_swing_open_metrics(signal)


                            notify_tp1(
                                state,
                                symbol,
                                direction,
                                entry,
                                tp1,
                            )

                    if (
                        tp1_hit
                        and not tp2_hit
                        and high >= tp2
                    ):
                        tp2_hit = True

                        sync_swing_open_metrics(signal)


                        notify_tp2(
                            state,
                            symbol,
                            direction,
                            tp2,
                        )

                    if (
                        tp1_hit
                        and not tp3_hit
                        and high >= tp3
                    ):
                        tp3_hit = True

                        sync_swing_open_metrics(signal)


                        notify_tp3(
                            state,
                            symbol,
                            direction,
                            tp3,
                        )

                        closed = True
                        break

                    if (
                        tp1_hit
                        and not just_hit_tp1
                        and (
                            tp1_hit_at <= 0
                            or candle_time > tp1_hit_at
                        )
                        and low <= entry
                    ):
                        sync_swing_open_metrics(signal)

                        notify_breakeven(
                            state,
                            symbol,
                            direction,
                            entry,
                        )

                        closed = True
                        break

                else:
                    if not tp1_hit:
                        if (
                            high >= sl
                            and low <= tp1
                        ):
                            if close <= entry:
                                tp1_hit = True
                                just_hit_tp1 = True
                                tp1_hit_at = (
                                    candle_time or now_ts()
                                )
                                signal["tp1_hit"] = True
                                signal["tp1_hit_at"] = tp1_hit_at

                                sync_swing_open_metrics(signal)


                                notify_tp1(
                                    state,
                                    symbol,
                                    direction,
                                    entry,
                                    tp1,
                                )
                            else:
                                sync_swing_open_metrics(signal)

                                add_swing_post_stop_follow(
                                    state,
                                    signal,
                                    close,
                                )

                                notify_stop(
                                    state,
                                    symbol,
                                    direction,
                                    entry,
                                    sl,
                                    close,
                                )
                                closed = True
                                break

                        elif high >= sl:
                            sync_swing_open_metrics(signal)

                            add_swing_post_stop_follow(
                                state,
                                signal,
                                close,
                            )

                            notify_stop(
                                state,
                                symbol,
                                direction,
                                entry,
                                sl,
                                close,
                            )
                            closed = True
                            break

                        elif low <= tp1:
                            tp1_hit = True
                            just_hit_tp1 = True
                            tp1_hit_at = (
                                candle_time or now_ts()
                            )
                            signal["tp1_hit"] = True
                            signal["tp1_hit_at"] = tp1_hit_at

                            sync_swing_open_metrics(signal)


                            notify_tp1(
                                state,
                                symbol,
                                direction,
                                entry,
                                tp1,
                            )

                    if (
                        tp1_hit
                        and not tp2_hit
                        and low <= tp2
                    ):
                        tp2_hit = True

                        sync_swing_open_metrics(signal)


                        notify_tp2(
                            state,
                            symbol,
                            direction,
                            tp2,
                        )

                    if (
                        tp1_hit
                        and not tp3_hit
                        and low <= tp3
                    ):
                        tp3_hit = True

                        sync_swing_open_metrics(signal)


                        notify_tp3(
                            state,
                            symbol,
                            direction,
                            tp3,
                        )

                        closed = True
                        break

                    if (
                        tp1_hit
                        and not just_hit_tp1
                        and (
                            tp1_hit_at <= 0
                            or candle_time > tp1_hit_at
                        )
                        and high >= entry
                    ):
                        sync_swing_open_metrics(signal)

                        notify_breakeven(
                            state,
                            symbol,
                            direction,
                            entry,
                        )

                        closed = True
                        break

            if closed:
                continue

            signal["symbol"] = symbol
            signal["opened_at"] = opened_at
            signal["last_checked_at"] = (
                now_ts()
            )
            signal["tp1_hit"] = tp1_hit
            signal["tp1_hit_at"] = tp1_hit_at
            signal["tp2_hit"] = tp2_hit
            signal["tp3_hit"] = tp3_hit

            sync_swing_open_metrics(signal)

            updated[key] = signal

        except Exception as exc:
            print(
                key,
                "swing takip hatası:",
                exc,
            )
            updated[key] = signal

    state["open_swing_signals"] = (
        updated
    )
    save_state(state)


# =========================================================
# RAPOR
# =========================================================

def top_reasons_text(
    counter,
    limit=5,
):
    if not counter:
        return "Veri yok"

    return "\n".join(
        f"• {reason}: {count}"
        for reason, count
        in counter.most_common(limit)
    )


def candidate_line(debug):
    if not debug:
        return ""

    missing = debug.get(
        "missing",
        [],
    )

    missing_text = (
        ", ".join(missing[:3])
        if missing
        else "eksik yok"
    )

    return (
        f"{debug['symbol']} "
        f"{debug['direction']} | "
        f"{debug.get('timing_mode')} | "
        f"şart {debug['ok_count']}/"
        f"{debug['total_conditions']} | "
        f"kalite {debug['score']}"
        f"/{debug.get('minimum_score')} | "
        f"risk "
        f"%{round(debug.get('risk_percent', 0), 2)} | "
        f"ADX 4H/1H "
        f"{round(debug.get('adx_4h', 0), 1)}/"
        f"{round(debug.get('adx_1h', 0), 1)} | "
        f"15M hacim "
        f"{round(debug.get('vol_15m', 0), 2)}x | "
        f"eksik: {missing_text}"
    )


def build_no_signal_report(
    scanned_count,
    new_signal_count,
    long_counter,
    short_counter,
    top_candidates,
):
    lines = [
        "📊 SWING RADAR v3 RAPORU",
        "",
        f"Bot: {BOT_NAME}",
        f"Zaman: {tr_now_text()}",
        f"Taranan coin: {scanned_count}",
        (
            "Filtreyi geçen kaliteli aday: "
            f"{new_signal_count}"
        ),
        "",
        "LONG tarafında en çok elenen:",
        top_reasons_text(
            long_counter
        ),
        "",
        "SHORT tarafında en çok elenen:",
        top_reasons_text(
            short_counter
        ),
        "",
        "Swing sinyale en yakın adaylar:",
    ]

    if top_candidates:
        for item in top_candidates[:8]:
            lines.append(
                "• "
                + candidate_line(item)
            )
    else:
        lines.append(
            "• Yakın aday yok"
        )

    lines.extend([
        "",
        (
            "Not: Bu rapor işlem sinyali değildir. "
            "Giriş, TP ve SL içeren gerçek Swing mesajını bekle."
        ),
    ])

    return "\n".join(lines)


def should_send_no_signal_report(state):
    if not SEND_NO_SIGNAL_REPORT:
        return False

    last_report = int(
        state.get(
            "last_no_signal_report",
            0,
        )
    )

    return (
        now_ts() - last_report
        >= NO_SIGNAL_REPORT_EVERY_MINUTES
        * 60
    )


def mark_no_signal_report_sent(state):
    state["last_no_signal_report"] = (
        now_ts()
    )
    save_state(state)


# =========================================================
# MAIN
# =========================================================

def signal_sort_key(signal):
    adx_strength = (
        safe_float(
            signal.get("adx_4h")
        )
        + safe_float(
            signal.get("adx_1h")
        )
    )

    volume_strength = max(
        safe_float(
            signal.get("vol_4h")
        ),
        safe_float(
            signal.get("vol_1h")
        ),
        safe_float(
            signal.get("vol_15m")
        ),
    )

    # Esit kalitede erken giris, yalnızca
    # diger kalite degerleri de iyiyse öne gelir.
    early_bonus = (
        1
        if signal.get("timing_mode")
        == "15M_ERKEN"
        else 0
    )

    return (
        safe_float(
            signal.get("score")
        ),
        -safe_float(
            signal.get(
                "risk_percent"
            ),
            999,
        ),
        early_bonus,
        adx_strength,
        volume_strength,
    )


def debug_sort_key(debug):
    if not debug:
        return (
            0,
            0,
            -999,
            0,
            0,
        )

    return (
        safe_float(
            debug.get("ok_count")
        ),
        safe_float(
            debug.get("score")
        ),
        -safe_float(
            debug.get(
                "risk_percent"
            ),
            999,
        ),
        safe_float(
            debug.get("adx_4h")
        )
        + safe_float(
            debug.get("adx_1h")
        ),
        max(
            safe_float(
                debug.get("vol_4h")
            ),
            safe_float(
                debug.get("vol_1h")
            ),
            safe_float(
                debug.get("vol_15m")
            ),
        ),
    )


def main():
    print(BOT_NAME, "başladı.")

    state = load_state()
    exchange = get_exchange()

    # Önce daha önce gönderilmiş Swing sinyallerinin
    # 30M/1H/4H/12H/24H yön performansı güncellenir.
    update_swing_performance(exchange)

    check_open_signals(
        exchange,
        state,
    )

    state = load_state()

    check_swing_post_stop_follow(
        exchange,
        state,
    )

    state = load_state()
    scan_coins = get_scan_coins(
        exchange
    )

    open_count = len(
        state.get(
            "open_swing_signals",
            {},
        )
    )

    available_slots = max(
        0,
        MAX_OPEN_SWING_SIGNALS
        - open_count,
    )

    print(
        "Açık swing:",
        open_count,
    )
    print(
        "Boş swing slot:",
        available_slots,
    )

    all_signals = []
    long_reasons = Counter()
    short_reasons = Counter()
    top_candidates = []

    scanned = 0

    for symbol in scan_coins:
        try:
            scanned += 1

            if has_open_same_symbol(
                state,
                symbol,
            ):
                print(
                    symbol,
                    "zaten açık swing var, atlandı.",
                )
                continue

            (
                signals,
                long_debug,
                short_debug,
            ) = analyze_symbol(
                exchange,
                symbol,
            )

            if long_debug:
                for reason in long_debug.get(
                    "missing",
                    [],
                ):
                    long_reasons[
                        reason
                    ] += 1

                top_candidates.append(
                    long_debug
                )

            if short_debug:
                for reason in short_debug.get(
                    "missing",
                    [],
                ):
                    short_reasons[
                        reason
                    ] += 1

                top_candidates.append(
                    short_debug
                )

            for signal in signals:
                if is_recent_duplicate(
                    state,
                    signal["symbol"],
                    signal["direction"],
                ):
                    print(
                        signal["symbol"],
                        signal["direction"],
                        "duplicate, atlandı.",
                    )
                    continue

                all_signals.append(
                    signal
                )

            time.sleep(0.08)

        except Exception as exc:
            print(
                symbol,
                "genel swing analiz hatası:",
                exc,
            )

    all_signals.sort(
        key=signal_sort_key,
        reverse=True,
    )

    top_candidates.sort(
        key=debug_sort_key,
        reverse=True,
    )

    selected = []

    max_to_send = min(
        MAX_NEW_SIGNALS_PER_RUN,
        available_slots,
    )

    # Gonderimden hemen once
    # giris bolgesi tekrar kontrol edilir.
    for signal in all_signals:
        if len(selected) >= max_to_send:
            break

        current_price = get_current_price(
            exchange,
            signal["symbol"],
        )

        if current_price is None:
            continue

        zone_drift = (
            entry_zone_distance_percent(
                current_price,
                signal["entry_low"],
                signal["entry_high"],
            )
        )

        allowed_drift = safe_float(
            signal.get(
                "max_zone_drift",
                MAX_ENTRY_ZONE_DRIFT_PERCENT,
            )
        )

        if zone_drift > allowed_drift:
            print(
                signal["symbol"],
                "Swing giriş bölgesinden uzaklaştı:",
                round(
                    zone_drift,
                    3,
                ),
                "%",
            )
            continue

        (
            direction_valid,
            direction_reason,
        ) = validate_direction_before_send(
            signal,
            current_price,
        )

        if not direction_valid:
            print(
                signal["symbol"],
                "Swing son yön kontrolünde elendi:",
                direction_reason,
            )
            continue

        # Sinyal üretiminden gönderime kadar
        # TP1 veya SL görülmüşse gönderme.
        if signal["direction"] == "LONG":
            if (
                current_price >= signal["tp1"]
                or current_price <= signal["sl"]
            ):
                print(
                    signal["symbol"],
                    "gönderim öncesi geçersiz oldu.",
                )
                continue
        else:
            if (
                current_price <= signal["tp1"]
                or current_price >= signal["sl"]
            ):
                print(
                    signal["symbol"],
                    "gönderim öncesi geçersiz oldu.",
                )
                continue

        portfolio_risk = (
            evaluate_portfolio_risk(
                symbol=signal["symbol"],
                direction=signal["direction"],
                source_bot="SWING",
            )
        )

        signal["portfolio_risk"] = (
            portfolio_risk
        )
        signal["portfolio_note"] = (
            format_portfolio_note(
                portfolio_risk
            )
        )

        if portfolio_risk.get(
            "hard_block",
            False,
        ):
            print(
                signal["symbol"],
                "portföy çakışması nedeniyle "
                "Swing sinyali elendi:",
                portfolio_risk.get(
                    "block_reason"
                ),
            )
            continue

        signal["current_price"] = (
            current_price
        )
        signal["zone_drift"] = (
            zone_drift
        )
        signal["direction_check"] = (
            direction_reason
        )

        selected.append(signal)

    print(
        "Taranan:",
        scanned,
        "| bulunan:",
        len(all_signals),
        "| açık:",
        open_count,
        f"/{MAX_OPEN_SWING_SIGNALS}",
        "| gönderilecek:",
        len(selected),
    )

    for signal in selected:
        message = (
            signal["message"]
            + "\n"
            + f"💰 Güncel Fiyat: "
            + f"{format_price(signal['current_price'])}\n"
            + f"📏 Giriş Bölgesi Sapması: "
            + f"%{round(signal['zone_drift'], 3)}\n"
            + "🧭 Son Yön Kontrolü: "
            + f"{signal['direction_check']} ✅\n"
            + "📌 Son Kontrol: "
            + "Swing giriş bölgesinde ve yapı geçerli ✅"
        )

        portfolio_note = signal.get(
            "portfolio_note"
        )

        if portfolio_note:
            message += (
                f"\n{portfolio_note}"
            )

        if send_telegram(message):
            record_id = record_swing_performance(
                signal
            )

            if record_id:
                signal["performance_record_id"] = (
                    record_id
                )

            save_open_signal(
                state,
                signal,
            )
            mark_sent(
                state,
                signal["symbol"],
                signal["direction"],
            )

            print(
                "Gönderildi:",
                signal["symbol"],
                signal["direction"],
                signal["timing_mode"],
                "skor",
                signal["score"],
            )

            time.sleep(1)

    if (
        not selected
        and should_send_no_signal_report(
            state
        )
    ):
        send_telegram(
            build_no_signal_report(
                scanned_count=scanned,
                new_signal_count=len(
                    all_signals
                ),
                long_counter=long_reasons,
                short_counter=short_reasons,
                top_candidates=top_candidates,
            )
        )

        mark_no_signal_report_sent(
            state
        )

    print(
        BOT_NAME,
        "tamamlandı.",
    )


if __name__ == "__main__":
    main()
