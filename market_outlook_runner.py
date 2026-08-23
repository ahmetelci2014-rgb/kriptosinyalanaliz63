"""Standalone runner for Market Outlook Engine with detailed V2 Telegram report."""
from __future__ import annotations

import os

import ccxt

import market_outlook_engine as outlook
from market_outlook_report_v2 import build_message as build_message_v2
from telegram_delivery import send_telegram_once


def build_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


def is_manual_workflow() -> bool:
    """GitHub Actions workflow_dispatch means the user explicitly asked for a report now."""
    return str(os.getenv("GITHUB_EVENT_NAME") or "").strip() == "workflow_dispatch"


def manual_delivery_key(snapshot) -> str:
    """Keep a manual run idempotent while allowing separate manual runs the same day."""
    run_id = str(os.getenv("GITHUB_RUN_ID") or "").strip()
    ts = int((snapshot or {}).get("ts") or 0)
    return f"MANUAL_{run_id or ts or 'LOCAL'}"


def send_manual_report_if_needed(result, token, chat_id) -> bool:
    """Send a fresh V2 report on manual dispatch even if today's daily report already exists.

    Scheduled runs keep the one-report-per-day rule in market_outlook_engine. A manual
    workflow is an explicit request for an additional current snapshot, so it gets its
    own delivery key. Re-running the same GitHub run remains duplicate-protected.
    """
    if not is_manual_workflow() or bool((result or {}).get("sent")):
        return False
    if not token or not chat_id:
        return False

    snapshot = (result or {}).get("snapshot") or {}
    if not snapshot:
        return False

    state_view = {"accuracy": (result or {}).get("accuracy") or {}}
    message = build_message_v2(snapshot, state_view)
    return bool(
        send_telegram_once(
            message=message,
            telegram_token=token,
            chat_id=chat_id,
            bot_key="MARKET_OUTLOOK",
            delivery_key=manual_delivery_key(snapshot),
        )
    )


def main() -> None:
    # V2 changes only the daily Telegram explanation. The scoring/forecast engine
    # remains untouched so historical accuracy stays comparable.
    outlook.build_message = build_message_v2

    token = os.getenv("TOKEN")
    chat_id = os.getenv("CHAT_ID")
    exchange = build_exchange()
    result = outlook.run(
        exchange,
        token=token,
        chat_id=chat_id,
    )

    manual_sent = send_manual_report_if_needed(result, token, chat_id)
    if manual_sent:
        result["sent"] = True

    snapshot = result.get("snapshot") or {}
    regime = snapshot.get("outlook") or {}
    print(
        "Market Outlook V2 tamamlandı | 6H:",
        regime.get("bias_6h"),
        "| 24H:",
        regime.get("bias_24h"),
        "| Telegram:",
        result.get("sent"),
        "| Manuel:",
        manual_sent,
    )


if __name__ == "__main__":
    main()
