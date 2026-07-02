import requests

TOKEN = "8619346423:AAFKQN6x6c1IreXc007VXvSB0gtemEwqhXg"
CHAT_ID = "8439391876"

try:
    btc = requests.get(
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    ).json()

    eth = requests.get(
        "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
    ).json()

    mesaj = f"""
🚀 KRİPTO SİNYAL BOTU

BTC: ${btc['bitcoin']['usd']}
ETH: ${eth['ethereum']['usd']}

✅ CoinGecko bağlantısı başarılı.
"""

except Exception as e:
    mesaj = f"❌ Hata:\n{e}"

requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    data={
        "chat_id": CHAT_ID,
        "text": mesaj
    }
)
