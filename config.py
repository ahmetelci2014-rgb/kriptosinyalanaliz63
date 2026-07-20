# config.py
# Premium MTF TP Odaklı v2
# 5M + 15M + 1H + 4H çoklu zaman dilimi futures sinyal botu.
# Emir açmaz. Sadece Telegram sinyali gönderir ve TP/SL takibi yapar.

BOT_NAME = "Premium MTF TP Odaklı v2"

# =========================
# TARAMA
# =========================
AUTO_TOP_VOLUME_SCAN = True
MAX_SCAN_COINS = 300
MIN_24H_QUOTE_VOLUME = 200_000

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
MAX_TRADE_SIGNALS_PER_RUN = 3
MAX_RADAR_ALERTS_PER_RUN = 0
MAX_OPEN_SIGNALS = 4

# Stop sayısı artarsa sistem durmaz, sadece daha seçici olur.
RISK_MODE_STOP_COUNT = 3
RISK_MODE_MAX_TRADE_SIGNALS = 1
RISK_MODE_MAX_RADAR_ALERTS = 0
RISK_MODE_ALLOW_RADAR_TRADE = False

# =========================
# FİLTRELER
# =========================
# 76 çok seçici kalıyordu; 72 daha dengeli.
MIN_SCORE_TRADE = 72

# Ana bot içi radar kapalı.
MIN_SCORE_RADAR = 999

# 4H / 1H trend şartı biraz gevşetildi.
MIN_ADX_4H = 10
MIN_ADX_1H = 10

# 0.60 orta seviye hacim filtresi.
MIN_VOLUME_RATIO_15M = 0.60

LONG_RSI_MIN = 40
LONG_RSI_MAX = 70
SHORT_RSI_MIN = 30
SHORT_RSI_MAX = 60

# 5M radar filtresi ana botta kapalı.
RADAR_MIN_5M_MOVE_PERCENT = 0.30
RADAR_MAX_5M_MOVE_PERCENT = 1.35
RADAR_MIN_VOLUME_RATIO = 1.15
RADAR_TRADE_MIN_SCORE = 999
RADAR_TRADE_MIN_VOLUME_RATIO = 999

# =========================
# RİSK / TP / SL
# =========================
MIN_RISK_PERCENT = 0.35
MAX_RISK_PERCENT = 2.40

# TP odaklı ayar:
# TP1 daha yakın, TP alma ihtimali daha yüksek.
TP1_R_MULTIPLIER = 0.55
TP2_R_MULTIPLIER = 1.05
TP3_R_MULTIPLIER = 1.60

# Giriş geç kalmışsa sinyal gönderme.
# 0.35 çok dar kalıyordu; 0.45 daha dengeli.
MAX_ENTRY_DISTANCE_PERCENT = 0.45

# TP1'e biraz yaklaşmış sinyali hemen elemesin.
MAX_TP1_PROGRESS_PERCENT = 55

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
    "TP odaklı MTF sürüm. "
    "4H ana trend + 1H onay + 15M giriş mantığı korunur. "
    "Ana bot içi radar kapalıdır. "
    "TP1 daha yakındır. "
    "Amaç daha sık TP1 yakalamaktır; kâr garantisi yoktur."
)


# TP_ODAKLI_V2_NOTU:
# Bu dosya, sinyal çok az geldiği için dengeli şekilde gevşetildi.
# Değişen ana noktalar:
# MIN_SCORE_TRADE = 72
# MIN_24H_QUOTE_VOLUME = 200_000
# MIN_ADX_4H = 10
# MIN_ADX_1H = 10
# MIN_VOLUME_RATIO_15M = 0.60
# MAX_RISK_PERCENT = 2.40
# TP1_R_MULTIPLIER = 0.55
# TP2_R_MULTIPLIER = 1.05
# TP3_R_MULTIPLIER = 1.60
# MAX_ENTRY_DISTANCE_PERCENT = 0.45
# MAX_TP1_PROGRESS_PERCENT = 55
# Ana bot içi radar kapalı kalır.
# Pump/Dump radar ayrı çalışır.