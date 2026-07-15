# config.py
# Sade Premium V1 - GEVŞETİLMİŞ Kontrollü LONG/SHORT
# Bu bot otomatik emir açmaz. Sadece Telegram sinyali gönderir.

# Zaman dilimleri
ENTRY_TIMEFRAME = "15m"
CONFIRM_TIMEFRAME = "1h"
TREND_TIMEFRAME = "4h"

# Veri limitleri
ENTRY_LIMIT = 400
CONFIRM_LIMIT = 300
TREND_LIMIT = 300

# Tarama kapsamı
# True olursa OKX'teki USDT swap/futures pariteleri içinden
# 24 saatlik hacmi en yüksek ilk MAX_SCAN_COINS coin taranır.
AUTO_TOP_VOLUME_SCAN = True
MAX_SCAN_COINS = 120

# Hacmi aşırı düşük pariteleri elemek için alt sınır.
# OKX hacim verisi okunamazsa bot yine ilk 80 sıralamayı kullanır.
MIN_24H_QUOTE_VOLUME = 500_000


# Sadece likiditesi yüksek ana coinler.
COINS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "ADAUSDT",
    "LTCUSDT",
    "DOTUSDT",
    "APTUSDT",
    "ARBUSDT",
    "OPUSDT",
    "NEARUSDT"
]

# Sistem ayarları
MAX_SIGNALS = 5

# SHORT tekrar açıldı ama daha sıkı filtreyle çalışır.
ALLOW_LONG_SIGNALS = True
ALLOW_SHORT_SIGNALS = True

# Kalite eşikleri
MIN_SCORE = 65
SHORT_MIN_SCORE = 75
MIN_ADX_4H = 15
MIN_ADX_1H = 15
MIN_VOLUME_RATIO = 0.60

# Stop mesafesi
MIN_RISK_PERCENT = 0.25
MAX_RISK_PERCENT = 2.80

# Geç giriş filtresi
MAX_ENTRY_DISTANCE_PERCENT = 0.50
MAX_TP1_PROGRESS_PERCENT = 55

# Tekrar sinyal engeli
DUPLICATE_BLOCK_SECONDS = 2 * 60 * 60

# Günlük rapor
DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 45

# Açık sinyal özeti
OPEN_SUMMARY_EVERY_MINUTES = 60
SEND_NO_SIGNAL_MESSAGE = True


# GEVSETME_NOTU:
# Bu sürüm sinyal sayısını artırmak için kontrollü gevşetildi.
# 80 yerine 120 hacimli coin tarar.
# Skor, ADX, hacim ve geç giriş filtreleri biraz yumuşatıldı.
# Otomatik emir açmaz; gelen sinyaller yine grafikte kontrol edilmelidir.
