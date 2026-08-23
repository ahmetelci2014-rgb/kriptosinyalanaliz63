"""Standalone runner for Market Outlook Engine with detailed V2 Telegram report."""
from __future__ import annotations

import os

import ccxt

import market_outlook_engine as outlook
from market_outlook_report_v2 import build_message as build_message_v2


def build_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


def main() -> None:
    # V2 changes only the daily Telegram explanation. The scoring/forecast engine
    # remains untouched so historical accuracy stays comparable.
    outlook.build_message = build_message_v2

    exchange = build_exchange()
    result = outlook.run(
        exchange,
        token=os.getenv("TOKEN"),
        chat_id=os.getenv("CHAT_ID"),
    )
    snapshot = result.get("snapshot") or {}
    regime = snapshot.get("outlook") or {}
    print(
        "Market Outlook V2 tamamlandı | 6H:",
        regime.get("bias_6h"),
        "| 24H:",
        regime.get("bias_24h"),
        "| Telegram:",
        result.get("sent"),
    )


if __name__ == "__main__":
    main()
