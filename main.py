import requests

TOKEN = "8619346423:AAGAXRkFwUD7Qy3l0MoggiKpOJzKOFtDZUY"
CHAT_ID = "8439391876"

mesaj = "✅ TEST MESAJI\nGitHub Actions başarılı çalıştı."

r = requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    data={
        "chat_id": CHAT_ID,
        "text": mesaj
    }
)

print(r.text)
