# config.py

# Tarama Ayarları
SCAN_INTERVAL = "15m"
INTERVAL = "15m"
LIMIT = 400

# Sinyal Ayarları
MIN_SCORE = 40
TOP_COINS = 40

# İşlem Yön Ayarları
# Backtest sonucuna göre SHORT tarafı şimdilik kapalı.
ALLOW_LONG_SIGNALS = True
ALLOW_SHORT_SIGNALS = False

# Kötü performans gösteren coinler
# Bu coinler otomatik taramada elenir.
BLACKLIST_COINS = [
    "ARMUSDT",
    "GALAUSDT",
    "TRXUSDT",
    "BERAUSDT",
    "BONKUSDT",
    "DYDXUSDT",
    "ATHUSDT",
    "BLURUSDT",
    "APEUSDT",
    "BANDUSDT"
]

# Zaman Dilimleri
MAIN_TREND_INTERVAL = "4H"
CONFIRM_INTERVAL = "1H"
ENTRY_INTERVAL = "15m"

# CoinGecko
VS_CURRENCY = "usd"
DAYS = 7

# OKX
OKX_BASE_URL = "https://www.okx.com"

# Premium / Yedek Coin Listesi
# main.py otomatik OKX USDT swap taraması yapar.
# Bu liste yedek ve premium öncelik listesi gibi kullanılır.
COINS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "SUIUSDT",
    "ADAUSDT",
    "LTCUSDT",
    "DOTUSDT",
    "APTUSDT",
    "ARBUSDT",
    "OPUSDT",
    "NEARUSDT",
    "INJUSDT",
    "WLDUSDT",
    "FILUSDT",
    "ATOMUSDT",
    "UNIUSDT",
    "AAVEUSDT",
    "ETCUSDT",
    "ICPUSDT",
    "SEIUSDT",
    "TIAUSDT",
    "ORDIUSDT",
    "JUPUSDT",
    "BCHUSDT",

    # Ek coinler
    "PEPEUSDT",
    "ALGOUSDT",
    "MANAUSDT",
    "SANDUSDT",
    "AXSUSDT"
]
