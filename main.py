import os
import requests

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

try:
    response = requests.get(
        "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    )

    data = response.json()

    if "price" in data:
        mesaj = f"""🚀 KRİPTO SİNYAL BOTU

Coin: BTCUSDT
Fiyat: {data['price']}

✅ Bot başarıyla çalışıyor.
"""
    else:
        mesaj = f"❌ Binance API Hatası:\n{data}"

except Exception as e:
    mesaj = f"❌ Hata oluştu:\n{e}"

requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    data={
        "chat_id": CHAT_ID,
        "text": mesaj
    }
)
