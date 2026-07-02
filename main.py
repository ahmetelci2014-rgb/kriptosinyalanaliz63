import requests

TOKEN = "8619346423:AAFKQN6x6c1IreXc007VXvSB0gtemEwqhXg"
CHAT_ID = "8439391876"

try:
    r = requests.get(
        "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
        timeout=10
    )

    veri = r.json()

    mesaj = f"Binance cevabı:\n{veri}"

except Exception as e:
    mesaj = f"Hata:\n{str(e)}"

requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    data={
        "chat_id": CHAT_ID,
        "text": mesaj
    }
)
