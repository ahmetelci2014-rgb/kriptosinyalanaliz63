"""Market Structure runner with the validated Smart trend veto.

This does not create a new trade route. It reuses the existing Market Structure
runner and suppresses informational WATCH/READY Telegram alerts only when the
100/200 two-step Smart structure trend is explicitly opposite to the alert.
Premium Core-Only live trading is untouched.
"""
from __future__ import annotations

from typing import Any, Dict

import market_structure_ai_shadow as structure
import market_structure_early_alerts as alerts
import market_structure_shadow_runner as runner
import smart_structure_adapter as smart

VERSION = "MARKET_STRUCTURE_SMART_VETO_V1_2026_08_26"
ENTRY_PERIOD = 100
TREND_PERIOD = 200

_ORIGINAL_OBSERVE = structure.observe
_ORIGINAL_SEND_EVENT = alerts.send_event


def _same_sign(direction: str) -> int:
    return 1 if str(direction).upper() == "LONG" else -1


def smart_context_opposes(event: Dict[str, Any]) -> bool:
    result = event.get("result") or {}
    context = result.get("smart_structure") or {}
    direction = str(result.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"} or not isinstance(context, dict):
        return False
    trend = int(context.get("trend") or 0)
    return trend == -_same_sign(direction)


def _observe_with_smart_context(symbol: str, df5m: Any, df15m: Any = None, current_price: Any = None):
    event = _ORIGINAL_OBSERVE(symbol, df5m, df15m, current_price)
    if not event:
        return event
    result = event.get("result") or {}
    context = smart.latest_features(
        df5m,
        entry_period=ENTRY_PERIOD,
        trend_period=TREND_PERIOD,
        exclude_open_candle=True,
    )
    result["smart_structure"] = context
    record = event.get("record") or {}
    record["smart_structure"] = context
    return event


def _send_event_with_smart_veto(event: Dict[str, Any], telegram_token: Any, chat_id: Any):
    if smart_context_opposes(event):
        result = event.get("result") or {}
        context = result.get("smart_structure") or {}
        print(
            "MARKET STRUCTURE SMART VETO:",
            result.get("symbol"),
            result.get("direction"),
            "smart_trend=",
            context.get("trend"),
            "break_count=",
            context.get("trend_break_count"),
        )
        return False, "SMART_TREND_OPPOSING"
    return _ORIGINAL_SEND_EVENT(event, telegram_token, chat_id)


def install() -> None:
    structure.observe = _observe_with_smart_context
    alerts.send_event = _send_event_with_smart_veto


def run() -> None:
    install()
    print(
        "MARKET STRUCTURE SMART VETO ACTIVE:",
        VERSION,
        "| entry_period=",
        ENTRY_PERIOD,
        "| trend_period=",
        TREND_PERIOD,
        "| only_opposite_trend_is_blocked=YES",
    )
    runner.run()


if __name__ == "__main__":
    run()
