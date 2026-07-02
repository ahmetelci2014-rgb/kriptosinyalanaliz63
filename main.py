import requests

TOKEN = "8619346423:AAFKQN6x6c1IreXc007VXvSB0gtemEwqhXg"
CHAT_ID = "8439391876"

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

response = requests.post(
    url,
    data={
        "chat_id": CHAT_ID,
        "text": "🚀 TEST BAŞARILI! GitHub Actions Telegram'a bağlandı."
    }
)

print(response.status_code)
print(response.text)
