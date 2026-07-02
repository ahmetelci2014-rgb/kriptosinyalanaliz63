import requests

TOKEN = "8619346423:AAFKQN6x6c1IreXc007VXvSB0gtemEwqhXg"
CHAT_ID = "8439391876"

coins = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "BNB": "binancecoin"
}

mesaj = "🚀 KRİPTO SİNYAL BOTU\n\n"

try:
    for sembol, coin in coins.items():

        veri = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
        ).json()

        fiyat = veri[coin]["usd"]

        # Basit test stratejisi
        if fiyat % 2 == 0:
            sinyal = "📈 LONG"
            tp = round(fiyat * 1.02, 2)
            sl = round(fiyat * 0.98, 2)
        else:
            sinyal = "📉 SHORT"
            tp = round(fiyat * 0.98, 2)
            sl = round(fiyat * 1.02, 2)

        mesaj += f"""
{sembol}
{sinyal}

Giriş: ${fiyat}
TP: ${tp}
SL: ${sl}

----------------
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
