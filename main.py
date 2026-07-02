import requests
import time

TOKEN = "8619346423:AAGAXRkFwUD7Qy3l0MoggiKpOJzKOFtDZUY"
CHAT_ID = "8439391876"

def telegram_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": mesaj
    })

def btc_fiyat():
    url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    r = requests.get(url).json()
    return r["price"]

telegram_gonder("🚀 Kripto Sinyal Botu Başlatıldı!")

while True:
    try:
        fiyat = btc_fiyat()
        telegram_gonder(f"📊 BTCUSDT Güncel Fiyat: {fiyat}")
        time.sleep(3600)
    except Exception as e:
        telegram_gonder(f"❌ Hata: {e}")
        time.sleep(60)
