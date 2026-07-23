# config.py
# Premium MTF TP Odaklı v2 - Canlı Para Dengeli Filtre
# 5M + 15M + 1H + 4H çoklu zaman dilimi futures sinyal botu.
# Emir açmaz. Sadece Telegram sinyali gönderir ve TP/SL takibi yapar.

BOT_NAME = "Premium MTF TP Odaklı v2"

# =========================
# TARAMA
# =========================

AUTO_TOP_VOLUME_SCAN = True

# 300 coin yerine daha hacimli ilk 180 coin taranır.
# Böylece tarama süresi ve düşük kaliteli coin sayısı azalır.
MAX_SCAN_COINS = 180

# Çok düşük hacimli ve kolay manipüle edilen coinleri azaltır.
MIN_24H_QUOTE_VOLUME = 1_000_000

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

# Her çalıştırmada yalnızca en güçlü 1 yeni sinyal gönderilir.
# Aynı anda çok sayıda işlem yığılmasını önler.
MAX_TRADE_SIGNALS_PER_RUN = 1

# Ana bot içi radar kapalı kalacak.
MAX_RADAR_ALERTS_PER_RUN = 0

# Bu değer botun takip ettiği riskli sinyallerin sınırıdır.
# Canlı hesapta aynı anda en fazla 2 gerçek işlem açılması önerilir.
MAX_OPEN_SIGNALS = 6

# =========================
# RİSK MODU
# =========================

# Aynı gün 5 stop sonrası risk modu devreye girer.
RISK_MODE_STOP_COUNT = 5

# Normal modda olduğu gibi risk modunda da yalnızca 1 sinyal gönderilir.
RISK_MODE_MAX_TRADE_SIGNALS = 1

RISK_MODE_MAX_RADAR_ALERTS = 0
RISK_MODE_ALLOW_RADAR_TRADE = False

# =========================
# FİLTRELER
# =========================

# Skoru şimdilik değiştirmiyoruz.
# Önce ADX, hacim, risk ve geç giriş filtrelerinin sonucunu gözlemleyeceğiz.
MIN_SCORE_TRADE = 72

# Ana bot içi radar kapalı.
MIN_SCORE_RADAR = 999

# ADX 10 yerine 15:
# Çok zayıf ve yönsüz trendlerin elenmesini sağlar.
MIN_ADX_4H = 15
MIN_ADX_1H = 15

# 15M hacmi kendi ortalamasının en az %75'i olmalı.
# Hacimsiz hareketler azaltılır.
MIN_VOLUME_RATIO_15M = 0.75

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

# Stop mesafesi %1.80'den fazlaysa sinyal kabul edilmez.
# Çok geniş stoplu işlemler azaltılır.
MAX_RISK_PERCENT = 1.80

# TP odaklı ayarlar korunuyor.
# Önce mevcut TP üretme yapısını bozmadan filtreleri güçlendiriyoruz.
TP1_R_MULTIPLIER = 0.55
TP2_R_MULTIPLIER = 1.05
TP3_R_MULTIPLIER = 1.60

# Fiyat hesaplanan girişten %0.35'ten fazla uzaklaşmışsa sinyal gönderilmez.
MAX_ENTRY_DISTANCE_PERCENT = 0.35

# Fiyat TP1 yolunun %45'inden fazlasını gitmişse geç giriş kabul edilir.
MAX_TP1_PROGRESS_PERCENT = 45

# =========================
# KALDIRAÇ ÖNERİSİ
# =========================

LEVERAGE_RISK_3X_MAX = 0.85
LEVERAGE_RISK_2X_MAX = 1.60

# Yeni maksimum risk sınırıyla uyumlu hâle getirildi.
LEVERAGE_RISK_1X2X_MAX = 1.80

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

# Aynı coin ve aynı yön için 90 dakika tekrar engeli.
TRADE_DUPLICATE_BLOCK_SECONDS = 90 * 60

RADAR_DUPLICATE_BLOCK_SECONDS = 45 * 60

# Stop olan coin 6 saat boyunca yeniden işlem sinyali üretemez.
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
    "Canlı para için dengeli filtrelenmiş TP odaklı MTF sürümü. "
    "4H ana trend + 1H onay + 15M giriş mantığı korunur. "
    "Ana bot içi radar kapalıdır. "
    "Düşük hacimli, zayıf trendli, geniş stoplu ve geç kalmış girişler azaltılmıştır. "
    "Her çalıştırmada yalnızca en güçlü 1 yeni işlem sinyali gönderilir. "
    "TP hedef yapısı korunmuştur; kâr garantisi yoktur."
)
