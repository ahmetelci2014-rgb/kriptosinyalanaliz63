# telegram_delivery.py
# Telegram sonuc teslim kilidi v1
#
# Amaç:
# - Aynı işlem + aynı sonuç (TP1/TP2/TP3/SL/BE) Telegram'a en fazla bir kez çıksın.
# - GitHub Actions runner'ı, local JSON state'i GitHub'a push etmeden kesilse bile
#   bir sonraki workflow aynı sonucu yeniden göndermesin.
#
# Mekanizma:
# - Gerçek işlem sonucundan ÖNCE, bot başına ayrı teslim dosyasına GitHub Contents API
#   üzerinden kalıcı claim yazılır.
# - Claim zaten varsa Telegram çağrısı yapılmaz ve sonuç "işlenmiş" kabul edilir.
# - Yeni işlem sinyalleri delivery_key olmadan gönderildiği için bu kilitten etkilenmez.
#
# Not: Telegram Bot API istemci tarafı idempotency anahtarı sunmadığı için ağın tam
# ortasında oluşan belirsiz bir kopmada matematiksel exactly-once garantisi mümkün
# değildir. Bu katman özellikle bizim gördüğümüz "Telegram gitti ama GitHub state
# push edilmedi" tekrar penceresini kapatır ve at-most-once davranışını tercih eder.

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests


DELIVERY_VERSION = "TELEGRAM_DELIVERY_V1_2026_08_09"
DELIVERY_KEEP_DAYS = 14
GITHUB_API_VERSION = "2022-11-28"
REMOTE_RETRY_COUNT = 5
REMOTE_TIMEOUT_SECONDS = 20
TELEGRAM_TIMEOUT_SECONDS = 20


def _now_ts():
    return int(time.time())


def _utc_text(timestamp=None):
    ts = _now_ts() if timestamp is None else int(timestamp)
    return datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _safe_bot_key(value):
    text = str(value or "BOT").strip().upper()
    cleaned = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_"
        for char in text
    )
    return cleaned or "BOT"


def _delivery_path(bot_key):
    return f"telegram_delivery_{_safe_bot_key(bot_key).lower()}.json"


def _empty_delivery(bot_key):
    return {
        "version": DELIVERY_VERSION,
        "bot": _safe_bot_key(bot_key),
        "claims": {},
        "last_update": 0,
    }


def _normalized_key(bot_key, delivery_key):
    return (
        f"{_safe_bot_key(bot_key)}|"
        f"{str(delivery_key or '').strip()}"
    )


def _message_hash(message):
    normalized = " ".join(str(message or "").split())
    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def _github_context():
    token = str(os.getenv("GITHUB_TOKEN") or "").strip()
    repository = str(os.getenv("GITHUB_REPOSITORY") or "").strip()
    branch = str(os.getenv("GITHUB_REF_NAME") or "main").strip() or "main"

    if not token or "/" not in repository:
        return None

    return {
        "token": token,
        "repository": repository,
        "branch": branch,
        "run_id": str(os.getenv("GITHUB_RUN_ID") or ""),
        "run_number": str(os.getenv("GITHUB_RUN_NUMBER") or ""),
        "workflow": str(os.getenv("GITHUB_WORKFLOW") or ""),
    }


def _github_headers(token):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def _decode_delivery_content(encoded, bot_key):
    try:
        raw = base64.b64decode(encoded or "").decode("utf-8")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    data.setdefault("version", DELIVERY_VERSION)
    data.setdefault("bot", _safe_bot_key(bot_key))
    data.setdefault("claims", {})
    data.setdefault("last_update", 0)

    if not isinstance(data.get("claims"), dict):
        data["claims"] = {}

    return data


def _prune_claims(data):
    claims = data.setdefault("claims", {})
    cutoff = _now_ts() - DELIVERY_KEEP_DAYS * 24 * 60 * 60

    for key, item in list(claims.items()):
        try:
            timestamp = int(
                (item or {}).get("claimed_at")
                if isinstance(item, dict)
                else 0
            )
        except Exception:
            timestamp = 0

        if timestamp and timestamp < cutoff:
            claims.pop(key, None)


def _fetch_remote_delivery(context, bot_key):
    path = _delivery_path(bot_key)
    url = (
        f"https://api.github.com/repos/{context['repository']}"
        f"/contents/{path}"
    )

    response = requests.get(
        url,
        headers=_github_headers(context["token"]),
        params={"ref": context["branch"]},
        timeout=REMOTE_TIMEOUT_SECONDS,
    )

    if response.status_code == 404:
        return _empty_delivery(bot_key), None

    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub teslim kaydı okunamadı: HTTP {response.status_code}"
        )

    payload = response.json()
    data = _decode_delivery_content(
        payload.get("content"),
        bot_key,
    )
    return data, payload.get("sha")


def _write_remote_delivery(
    context,
    bot_key,
    data,
    sha,
    commit_message,
):
    path = _delivery_path(bot_key)
    url = (
        f"https://api.github.com/repos/{context['repository']}"
        f"/contents/{path}"
    )

    content = json.dumps(
        data,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"

    body = {
        "message": commit_message,
        "content": base64.b64encode(
            content.encode("utf-8")
        ).decode("ascii"),
        "branch": context["branch"],
    }

    if sha:
        body["sha"] = sha

    return requests.put(
        url,
        headers=_github_headers(context["token"]),
        json=body,
        timeout=REMOTE_TIMEOUT_SECONDS,
    )


def _claim_remote(bot_key, delivery_key, message):
    context = _github_context()

    if context is None:
        print(
            "Telegram kalıcı teslim kilidi için GITHUB_TOKEN/"
            "GITHUB_REPOSITORY eksik. Sonuç mesajı güvenlik gereği gönderilmedi."
        )
        return "ERROR"

    normalized_key = _normalized_key(
        bot_key,
        delivery_key,
    )

    for attempt in range(1, REMOTE_RETRY_COUNT + 1):
        try:
            data, sha = _fetch_remote_delivery(
                context,
                bot_key,
            )
            _prune_claims(data)
            claims = data.setdefault("claims", {})

            if normalized_key in claims:
                print(
                    "Telegram duplicate kalıcı teslim kilidiyle engellendi:",
                    normalized_key,
                )
                return "DUPLICATE"

            now = _now_ts()
            claims[normalized_key] = {
                "claimed_at": now,
                "claimed_at_utc": _utc_text(now),
                "message_hash": _message_hash(message),
                "run_id": context.get("run_id"),
                "run_number": context.get("run_number"),
                "workflow": context.get("workflow"),
                "status": "CLAIMED_BEFORE_SEND",
            }
            data["version"] = DELIVERY_VERSION
            data["bot"] = _safe_bot_key(bot_key)
            data["last_update"] = now

            event_label = str(delivery_key or "RESULT").split("|")[-1]
            response = _write_remote_delivery(
                context,
                bot_key,
                data,
                sha,
                (
                    f"Claim Telegram result "
                    f"{_safe_bot_key(bot_key)} {event_label}"
                ),
            )

            if response.status_code in (200, 201):
                print(
                    "Telegram kalıcı teslim claim kaydedildi:",
                    normalized_key,
                )
                return "CLAIMED"

            if response.status_code in (409, 422):
                print(
                    "Telegram teslim claim çakıştı; tekrar deneniyor:",
                    attempt,
                )
                time.sleep(min(1.5 * attempt, 5.0))
                continue

            print(
                "Telegram teslim claim GitHub hatası:",
                response.status_code,
            )
            time.sleep(min(1.5 * attempt, 5.0))

        except Exception as exc:
            print(
                "Telegram teslim claim hatası:",
                exc,
                "deneme:",
                attempt,
            )
            time.sleep(min(1.5 * attempt, 5.0))

    return "ERROR"


def send_telegram_once(
    message,
    telegram_token,
    chat_id,
    bot_key,
    delivery_key=None,
):
    """
    Yeni işlem sinyallerinde delivery_key=None kullanılır ve mesaj normal gider.
    TP/SL/BE sonuçlarında delivery_key zorunlu verilerek kalıcı GitHub claim alınır.

    Dönüş:
    - True: mesaj gönderildi veya aynı delivery_key daha önce claim edildi.
    - False: Telegram bilgisi eksik, claim alınamadı veya HTTP çağrısı başarısız.
    """
    if not telegram_token or not chat_id:
        print("TOKEN veya CHAT_ID eksik.")
        return False

    if delivery_key:
        claim_status = _claim_remote(
            bot_key,
            delivery_key,
            message,
        )

        if claim_status == "DUPLICATE":
            return True

        if claim_status != "CLAIMED":
            print(
                "Telegram sonuç mesajı teslim kilidi alınamadığı için ertelendi."
            )
            return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{telegram_token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": str(message),
            },
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )

        print("Telegram cevap:", response.status_code)
        return response.status_code == 200

    except Exception as exc:
        # At-most-once tercihi: kalıcı claim silinmez. Ağın belirsiz anında
        # Telegram mesajı aslında kabul edilmiş olabilir; otomatik retry duplicate
        # üretebileceği için sonraki çalışmada tekrar gönderilmez.
        print("Telegram gönderim hatası:", exc)
        return False
