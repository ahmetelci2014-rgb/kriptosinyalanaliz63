"""Send a low-frequency Telegram status only when the bot would otherwise stay silent.

This does not create trade signals, alter admission filters, or place orders.
It exists only so the user can verify the live scanner is still healthy.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATE_FILE = Path("market_first_telegram_heartbeat_state.json")
HEALTH_FILE = Path("market_first_scheduler_health.json")
DIAG_FILE = Path("market_first_diagnostics.json")
DELIVERY_FILE = Path("market_first_delivery_truth.json")
TELEGRAM_DELIVERY_FILE = Path("telegram_delivery_market_first_v5.json")
COOLDOWN_SECONDS = 2 * 60 * 60


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _send(text: str) -> bool:
    token = os.environ.get("TOKEN", "").strip()
    chat_id = os.environ.get("CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM HEARTBEAT: TOKEN/CHAT_ID eksik; gönderilmedi.")
        return False

    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ok = 200 <= int(resp.status) < 300
            print("TELEGRAM HEARTBEAT cevap:", resp.status)
            return ok
    except Exception as exc:
        print("TELEGRAM HEARTBEAT hata:", type(exc).__name__, str(exc))
        return False


def main() -> None:
    now = int(time.time())
    state = _load(STATE_FILE)
    last_sent = int(state.get("last_sent_at") or 0)
    if now - last_sent < COOLDOWN_SECONDS:
        print("TELEGRAM HEARTBEAT: kendi cooldown aktif.")
        return

    delivery_state = _load(TELEGRAM_DELIVERY_FILE)
    last_user_message = int(delivery_state.get("last_update") or 0)
    if last_user_message and now - last_user_message < COOLDOWN_SECONDS:
        print("TELEGRAM HEARTBEAT: yakin zamanda kullanici mesaji var; heartbeat gereksiz.")
        return

    health = _load(HEALTH_FILE)
    diag = _load(DIAG_FILE)
    delivery = _load(DELIVERY_FILE)

    status = str(health.get("status") or "UNKNOWN")
    market = diag.get("market") if isinstance(diag.get("market"), dict) else {}
    market_label = str(market.get("regime") or diag.get("market_regime") or "Bilinmiyor")
    preferred = str(market.get("preferred_direction") or diag.get("market_preferred_direction") or "-")

    top = diag.get("top_decisions") if isinstance(diag.get("top_decisions"), list) else []
    actionable = [x for x in top if isinstance(x, dict)]
    closest = []
    for row in actionable[:3]:
        symbol = row.get("symbol")
        direction = row.get("direction")
        score = row.get("score")
        if symbol and direction:
            closest.append(f"{symbol} {direction} ({score})")
    closest_text = ", ".join(closest) if closest else "uygun yakın aday yok"

    funnel = delivery.get("funnel_truth") if isinstance(delivery.get("funnel_truth"), dict) else {}
    real_sent = int(funnel.get("real_signal_sent") or 0)

    message = (
        "🟢 MARKET FIRST AKTİF\n\n"
        f"⚙️ Tarayıcı: {status}\n"
        f"📊 Piyasa: {market_label} | Tercih: {preferred}\n"
        f"🚨 Bu kontrolde gerçek işlem sinyali: {real_sent}\n"
        f"👀 En yakın izlenenler: {closest_text}\n\n"
        "ℹ️ Bu bir işlem sinyali değildir. Kaliteli giriş oluşursa ayrıca işlem mesajı gelir."
    )

    if _send(message):
        _save({"last_sent_at": now, "last_message": message})
        print("TELEGRAM HEARTBEAT gönderildi.")


if __name__ == "__main__":
    main()
