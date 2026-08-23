"""Standalone runner for Market Outlook V3 research + clear Telegram summary."""
from __future__ import annotations

import os

import ccxt

import market_outlook_engine as outlook
from market_outlook_report_v3 import build_message as build_message_v3
from market_outlook_research_v3 import derive_research
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


def persist_research(result):
    """Attach deep V3 research to the current snapshot so the background evidence is retained."""
    snapshot = (result or {}).get("snapshot") or {}
    state = outlook.load_state()
    if not snapshot:
        return state, {}

    research = derive_research(snapshot, state)
    ts = int(snapshot.get("ts") or 0)
    for row in reversed(state.get("snapshots") or []):
        if isinstance(row, dict) and int(row.get("ts") or 0) == ts:
            row["research_v3"] = research
            break
    outlook.atomic_save(outlook.STATE_FILE, state)
    return state, research


def send_manual_report_if_needed(result, token, chat_id, state_view) -> bool:
    """Send a fresh V3 report on manual dispatch even if today's daily report already exists."""
    if not is_manual_workflow() or bool((result or {}).get("sent")):
        return False
    if not token or not chat_id:
        return False

    snapshot = (result or {}).get("snapshot") or {}
    if not snapshot:
        return False

    message = build_message_v3(snapshot, state_view or {})
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
    # V3 changes explanation + background historical interpretation only. The
    # direction/forecast score engine remains untouched so accuracy stays comparable.
    outlook.build_message = build_message_v3

    token = os.getenv("TOKEN")
    chat_id = os.getenv("CHAT_ID")
    exchange = build_exchange()
    result = outlook.run(
        exchange,
        token=token,
        chat_id=chat_id,
    )

    state_view, research = persist_research(result)
    manual_sent = send_manual_report_if_needed(result, token, chat_id, state_view)
    if manual_sent:
        result["sent"] = True

    snapshot = result.get("snapshot") or {}
    regime = snapshot.get("outlook") or {}
    print(
        "Market Outlook V3 tamamlandı | 6H:",
        regime.get("bias_6h"),
        "| 24H:",
        regime.get("bias_24h"),
        "| Pulse:",
        research.get("pulse"),
        "| Telegram:",
        result.get("sent"),
        "| Manuel:",
        manual_sent,
    )


if __name__ == "__main__":
    main()
