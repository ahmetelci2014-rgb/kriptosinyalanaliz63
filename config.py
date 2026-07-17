# config.py
# Premium MTF Futures Bot v1
# 5M + 15M + 1H + 4H çoklu zaman dilimi futures sinyal botu.
# Emir açmaz. Sadece Telegram sinyali gönderir ve TP/SL takibi yapar.

BOT_NAME = "Premium MTF Futures Bot v1"

# =========================
# TARAMA
# =========================
AUTO_TOP_VOLUME_SCAN = True
MAX_SCAN_COINS = 220
MIN_24H_QUOTE_VOLUME = 300_000

# Öncelikli coinler önce taranır, sonra hacimli diğer coinler eklenir.
PRIORITY_COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT", "ADAUSDT",
    "LTCUSDT", "DOTUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "NEARUSDT", "INJUSDT", "WLDUSDT", "FILUSDT", "ATOMUSDT",
    "UNIUSDT", "AAVEUSDT", "TRXUSDT", "ETCUSDT", "ICPUSDT",
    "SEIUSDT", "TIAUSDT", "JUPUSDT", "BCHUSDT"
]

ALLOW_LONG = True
ALLOW_SHORT = True

# =========================
# ZAMAN DİLİMLERİ
# =========================
RADAR_TIMEFRAME = "5m"
ENTRY_TIMEFRAME = "15m"
CONFIRM_TIMEFRAME = "1h"
TREND_TIMEFRAME = "4h"
TRACK_TIMEFRAME = "5m"

RADAR_LIMIT = 180
ENTRY_LIMIT = 280
CONFIRM_LIMIT = 240
TREND_LIMIT = 240
TRACK_LIMIT = 180

# =========================
# SİNYAL SAYISI
# =========================
MAX_TRADE_SIGNALS_PER_RUN = 2
MAX_RADAR_ALERTS_PER_RUN = 0
MAX_OPEN_SIGNALS = 2

# Stop sayısı artarsa sistem durmaz, sadece daha seçici olur.
RISK_MODE_STOP_COUNT = 3
RISK_MODE_MAX_TRADE_SIGNALS = 1
RISK_MODE_MAX_RADAR_ALERTS = 0
RISK_MODE_ALLOW_RADAR_TRADE = False

# =========================
# FİLTRELER
# =========================
MIN_SCORE_TRADE = 76
MIN_SCORE_RADAR = 64

MIN_ADX_4H = 12
MIN_ADX_1H = 12
MIN_VOLUME_RATIO_15M = 1.00

LONG_RSI_MIN = 40
LONG_RSI_MAX = 70
SHORT_RSI_MIN = 30
SHORT_RSI_MAX = 60

# 5M radar filtresi
RADAR_MIN_5M_MOVE_PERCENT = 0.30
RADAR_MAX_5M_MOVE_PERCENT = 1.35
RADAR_MIN_VOLUME_RATIO = 1.15
RADAR_TRADE_MIN_SCORE = 86
RADAR_TRADE_MIN_VOLUME_RATIO = 1.60

# =========================
# RİSK / TP / SL
# =========================
MIN_RISK_PERCENT = 0.45
MAX_RISK_PERCENT = 1.80

TP1_R_MULTIPLIER = 0.75
TP2_R_MULTIPLIER = 1.35
TP3_R_MULTIPLIER = 2.00

# Giriş geç kalmışsa sinyal gönderme
MAX_ENTRY_DISTANCE_PERCENT = 0.35
MAX_TP1_PROGRESS_PERCENT = 40

# Kaldıraç önerisi
LEVERAGE_RISK_3X_MAX = 0.85
LEVERAGE_RISK_2X_MAX = 1.60
LEVERAGE_RISK_1X2X_MAX = 2.40

# =========================
# MARKET KORUMA
# =========================
MARKET_GUARD_ENABLED = True
MARKET_REFERENCE_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
MARKET_LONG_MIN_OK_COUNT = 2
MARKET_SHORT_MIN_OK_COUNT = 2
MARKET_MAX_COUNTER_5M_MOVE_PERCENT = 0.40

# =========================
# TEKRAR / COOLDOWN
# =========================
TRADE_DUPLICATE_BLOCK_SECONDS = 90 * 60
RADAR_DUPLICATE_BLOCK_SECONDS = 45 * 60
STOPPED_COIN_COOLDOWN_HOURS = 8

# =========================
# TAKİP / RAPOR
# =========================
MAX_OPEN_SIGNAL_HOURS = 18
SEND_STATUS_EVERY_MINUTES = 60
OPEN_SUMMARY_EVERY_MINUTES = 90

DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 45

# =========================
# SİSTEM NOTU
# =========================
SYSTEM_NOTE = (
    "4H ana trend + 1H onay + 15M giriş + 5M radar. "
    "A kalite işlem ve radar uyarısı ayrıdır. "
    "Sistem stopta durmaz, risk modunda daha seçici devam eder."
)


# RADAR_MESAJLARI_KAPALI:
# Kalabalık yapmaması için işlem olmayan radar uyarıları kapatıldı.
# 5M radar verisi analiz içinde kullanılmaya devam eder.
# Telegram'a sadece A kalite işlem sinyalleri, TP/SL takipleri ve raporlar gelir.


# HACIM_RISK_FILTRESI_NOTU:
# PNUTUSDT örneğinde 15M hacim 0.66x olmasına rağmen sinyal geldi ve stop oldu.
# Bu sürümde A kalite işlem için 15M hacim en az 1.00x olmalı.
# Stop mesafesi %1.80 üstündeyse işlem sinyali üretilmez.
# Aynı anda maksimum 2 açık işlem tutulur.
# Radar mesajları kapalı kalır.
