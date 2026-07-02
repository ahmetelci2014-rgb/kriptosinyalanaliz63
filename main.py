import requests

TOKEN = "8619346423:AAFKQN6x6c1IreXc007VXvSB0gtemEwqhXg"
CHAT_ID = "8439391876"

try:
    btc = requests.get(
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    ).json()

    fiyat = btc["bitcoin"]["usd"]

    if fiyat > 60000:
        sinyal = "📈 LONG"
    else:
        sinyal = "📉 SHORT"

    mesaj = f"""
🚀 KRİPTO SİNYAL BOTU

Coin: BTC
Fiyat: ${fiyat}

Sinyal: {sinyal}

⚠️ Test sinyalidir.
"""

except Exception as e:
    mesaj = f"❌ Hata: {e}"

requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    data={
        "chat_id": CHAT_ID,
        "text": mesaj
    }
)
