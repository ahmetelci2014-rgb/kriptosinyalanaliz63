import requests
import time

TOKEN = "8619346423:AAHyaf5nk3IQYvMzEcNAYQFQH8eALdz6220"
CHAT_ID = "8439391876"

coins = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "BNB": "binancecoin"
}

while True:
    mesaj = "🚀 KRİPTO SİNYAL BOTU\n\n"

    try:
        for sembol, coin in coins.items():
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
            veri = requests.get(url, timeout=10).json()

            fiyat = veri[coin]["usd"]

            if int(fiyat) % 2 == 0:
                sinyal = "🟢 LONG"
                tp = round(fiyat * 1.02, 2)
                sl = round(fiyat * 0.98, 2)
            else:
                sinyal = "🔴 SHORT"
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

    time.sleep(900)
