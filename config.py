# config.py
# Premium GitHub V2
# GitHub Actions ile 5 dakikada bir çalışacak sade ama güçlü sinyal botu.
# Bot otomatik emir açmaz. Sadece Telegram sinyali gönderir.

BOT_NAME = "Premium GitHub V2 - Market Koruma"

# Tarama
AUTO_TOP_VOLUME_SCAN = True
MAX_SCAN_COINS = 120
MIN_24H_QUOTE_VOLUME = 500_000

# Zaman dilimleri
RADAR_TIMEFRAME = "5m"
ENTRY_TIMEFRAME = "15m"
CONFIRM_TIMEFRAME = "1h"
TREND_TIMEFRAME = "4h"

RADAR_LIMIT = 160
ENTRY_LIMIT = 420
CONFIRM_LIMIT = 320
TREND_LIMIT = 320

# Yedek / öncelikli coin listesi
COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "ADAUSDT", "LTCUSDT",
    "DOTUSDT", "ARBUSDT", "OPUSDT", "NEARUSDT", "INJUSDT"
]

# Yönler
ALLOW_LONG = True
ALLOW_SHORT = True

# Gönderim
MAX_SIGNALS_PER_RUN = 3
MAX_OPEN_SIGNALS = 3
SEND_STATUS_EVERY_MINUTES = 60

# Normal premium sinyal eşikleri
TRADE_MIN_SCORE = 76
MIN_ADX_4H = 15
MIN_ADX_1H = 15
MIN_VOLUME_RATIO = 0.60

# Radar hızlı giriş eşikleri
RADAR_ENABLED = True
RADAR_MIN_SCORE = 72
RADAR_MIN_5M_MOVE_PERCENT = 0.35
RADAR_MAX_5M_MOVE_PERCENT = 1.15
RADAR_MIN_15M_MOVE_PERCENT = 0.15
RADAR_MIN_VOLUME_RATIO = 1.20
RADAR_MAX_CURRENT_FROM_CLOSE_PERCENT = 0.20

# Stop azaltma filtresi
# BILL gibi dipten SHORT ve ZORA gibi tepeden LONG sinyallerini azaltmak için.
RADAR_LONG_MAX_RSI = 70
RADAR_SHORT_MIN_RSI = 35

# Aynı coin bugün stop olduysa o coin için yeni sinyal gönderme.
BLOCK_COIN_AFTER_DAILY_STOP = True

# Risk
MIN_RISK_PERCENT = 0.25
MAX_RISK_PERCENT = 2.80
RADAR_MIN_RISK_PERCENT = 0.40
RADAR_MAX_RISK_PERCENT = 2.60

# Geç giriş engeli
MAX_ENTRY_DISTANCE_PERCENT = 0.30
MAX_TP1_PROGRESS_PERCENT = 35

# Tekrar sinyal engeli
DUPLICATE_BLOCK_SECONDS = 2 * 60 * 60
RADAR_DUPLICATE_BLOCK_SECONDS = 45 * 60

# Açık sinyal takip
MAX_OPEN_SIGNAL_HOURS = 24
OPEN_SUMMARY_EVERY_MINUTES = 90

# Günlük rapor
DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 45

# Güvenlik / disiplin
MAX_DAILY_STOP_ALERTS = 2


# STOP_FILTRESI_NOTU:
# Bu sürüm; BILL, ZORA, ATH ve ROBO stoplarından sonra hazırlanmıştır.
# Eklenen mantık:
# 1) RADAR LONG sinyali RSI 70 üstündeyse gönderilmez.
# 2) RADAR SHORT sinyali RSI 35 altındaysa gönderilmez.
# 3) Bir coin aynı gün stop olduysa, o coin gün bitene kadar tekrar sinyal üretmez.
# Normal 4H/1H/15M onaylı sistem aynen korunmuştur.


# Market koruma filtresi
# NEAR ve LTC gibi aynı anda gelen LONG stopları, genel piyasa geri çekilmesi riskini gösterir.
MARKET_GUARD_ENABLED = True
MARKET_REFERENCE_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# LONG için piyasa şartı:
# En az 2 referans coin 15M'de EMA20 üstünde olmalı ve son 5M sert kırmızı olmamalı.
MARKET_LONG_MIN_OK_COUNT = 2

# SHORT için piyasa şartı:
# En az 2 referans coin 15M'de EMA20 altında olmalı ve son 5M sert yeşil olmamalı.
MARKET_SHORT_MIN_OK_COUNT = 2

# Referans coin 5M mum ters yönde bu orandan fazla hareket ederse o yön bloklanır.
MARKET_MAX_COUNTER_5M_MOVE_PERCENT = 0.35

# Aynı yönde günlük stop limiti.
# Örn: 2 LONG stop olduysa o gün yeni LONG sinyali gönderilmez.
DAILY_DIRECTION_STOP_LIMIT = 2

# MARKET_KORUMA_NOTU:
# Bu sürüm NEAR/LTC gibi aynı anda gelen LONG stoplarından sonra hazırlanmıştır.
# Amaç, genel piyasa aşağı dönerken yeni LONG sinyalini kesmektir.
