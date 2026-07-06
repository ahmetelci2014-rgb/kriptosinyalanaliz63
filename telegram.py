import os
import requests


def send_message(message):
    token = os.getenv("TOKEN")
    chat_id = os.getenv("CHAT_ID")

    if not token or not chat_id:
        print("TOKEN veya CHAT_ID eksik.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message
    }

    try:
        response = requests.post(url, data=payload, timeout=20)

        if response.status_code != 200:
            print(f"Telegram gönderim hatası: {response.text}")

    except Exception as e:
        print(f"Telegram bağlantı hatası: {e}")
