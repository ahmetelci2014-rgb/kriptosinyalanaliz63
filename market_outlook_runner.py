"""Standalone runner for Market Outlook Engine V1."""
from __future__ import annotations

import os

import ccxt

import market_outlook_engine as outlook


def build_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


def main() -> None:
    exchange = build_exchange()
    result = outlook.run(
        exchange,
        token=os.getenv("TOKEN"),
        chat_id=os.getenv("CHAT_ID"),
    )
    snapshot = result.get("snapshot") or {}
    regime = snapshot.get("outlook") or {}
    print(
        "Market Outlook tamamlandı | 6H:",
        regime.get("bias_6h"),
        "| 24H:",
        regime.get("bias_24h"),
        "| Telegram:",
        result.get("sent"),
    )


if __name__ == "__main__":
    main()
