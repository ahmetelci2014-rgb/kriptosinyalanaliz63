# config.py
# Premium MTF TP Odaklı v2 - Stabil TP Geri Dönüş
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

# Eski TP akışını öldürmemek için 3 korunuyor.
MAX_TRADE_SIGNALS_PER_RUN = 3

# Ana bot içi radar kapalı kalacak.
MAX_RADAR_ALERTS_PER_RUN = 0

# 4 çok dar kalıyordu.
# TP1 görmüş işlemler hâlâ takipte kaldığı için yeni sinyali kesebiliyordu.
MAX_OPEN_SIGNALS = 6

# =========================
# RİSK MODU
# =========================

# 3 stop sonrası risk modu erken devreye giriyordu.
# Bu da 39 aday bulsa bile 1 sinyal göndermesine sebep olabiliyordu.
RISK_MODE_STOP_COUNT = 5

# Risk modu aktif olsa bile 1 sinyal çok az kalıyordu.
RISK_MODE_MAX_TRADE_SIGNALS = 2

RISK_MODE_MAX_RADAR_ALERTS = 0
RISK_MODE_ALLOW_RADAR_TRADE = False

# =========================
# FİLTRELER
# =========================

# 72 TP odaklı v2 için dengeli.
MIN_SCORE_TRADE = 72

# Ana bot içi radar kapalı.
MIN_SCORE_RADAR = 999

# 4H / 1H trend şartı dengeli.
MIN_ADX_4H = 10
MIN_ADX_1H = 10

# 0.60 korunuyor.
# INJ / RAY gibi düşük hacimli ama TP yapan sinyaller kaçmasın.
MIN_VOLUME_RATIO_15M = 0.60

LONG_RSI_MIN = 40
LONG_RSI_MAX = 70

SHORT_RSI_MIN = 30
SHORT_RSI_MAX = 60

# =========================
# 5M RADAR
# =========================

# Ana bot içi radar kapalı.
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
# TP1 yakın kalsın, çünkü sistemde en iyi çalışan kural bu.
TP1_R_MULTIPLIER = 0.55
TP2_R_MULTIPLIER = 1.05
TP3_R_MULTIPLIER = 1.60

# Giriş geç kalmışsa sinyal gönderme.
MAX_ENTRY_DISTANCE_PERCENT = 0.45

# TP1'e yaklaşmış sinyali hemen elemesin.
MAX_TP1_PROGRESS_PERCENT = 55

# =========================
# KALDIRAÇ ÖNERİSİ
# =========================

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

# 90 dakika korunuyor.
# Çok uzatırsak çalışan tekrar sinyaller kaçabilir.
TRADE_DUPLICATE_BLOCK_SECONDS = 90 * 60

RADAR_DUPLICATE_BLOCK_SECONDS = 45 * 60

# 8 saat biraz sert kalabilir.
# Stop olan coin tamamen gün boyu ölmesin ama hemen de tekrar gelmesin.
STOPPED_COIN_COOLDOWN_HOURS = 6

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
    "TP odaklı MTF stabil geri dönüş sürümü. "
    "4H ana trend + 1H onay + 15M giriş mantığı korunur. "
    "Ana bot içi radar kapalıdır. "
    "TP1 daha yakındır. "
    "Risk modu yumuşatıldı. "
    "Amaç eski TP akışına yaklaşmaktır; kâr garantisi yoktur."
)