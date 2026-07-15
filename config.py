# config.py
# Sade Premium V1
# Amaç: Karmaşık ve zayıf sistemi bırakıp daha kontrollü sinyal üretmek.
# Bu bot otomatik emir açmaz. Sadece Telegram sinyali gönderir.

# Zaman dilimleri
ENTRY_TIMEFRAME = "15m"
CONFIRM_TIMEFRAME = "1h"
TREND_TIMEFRAME = "4h"

# Veri limitleri
ENTRY_LIMIT = 400
CONFIRM_LIMIT = 300
TREND_LIMIT = 300

# Sadece likiditesi yüksek ana coinler.
# Tüm piyasayı taramak yerine önce daha temiz verili coinlerde kalite arıyoruz.
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
MAX_SIGNALS = 2

# Backtestte SHORT tarafı kötü çıktığı için kapalı.
ALLOW_LONG_SIGNALS = True
ALLOW_SHORT_SIGNALS = False

# Kalite eşikleri
MIN_SCORE = 75
MIN_ADX_4H = 18
MIN_ADX_1H = 18
MIN_VOLUME_RATIO = 0.75

# Stop mesafesi
MIN_RISK_PERCENT = 0.35
MAX_RISK_PERCENT = 2.40

# Geç giriş filtresi
# Sinyal üretildikten sonra canlı fiyat girişten çok uzaksa Telegram'a gönderilmez.
MAX_ENTRY_DISTANCE_PERCENT = 0.35
MAX_TP1_PROGRESS_PERCENT = 40

# Tekrar sinyal engeli
DUPLICATE_BLOCK_SECONDS = 4 * 60 * 60

# Günlük rapor
DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 45

# Açık sinyal özeti
OPEN_SUMMARY_EVERY_MINUTES = 120
SEND_NO_SIGNAL_MESSAGE = True
