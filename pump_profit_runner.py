"""Pump/Dump shadow data mode.

Premium MTF is the only live Telegram trade channel. Pump/Dump keeps scanning,
creating virtual/shadow entries and updating its performance ledger, but every
Telegram message is suppressed. No orders.
"""
from __future__ import annotations
from typing import Any

import market_impulse_guard as impulse
import opportunity_capture as capture
import profitability_engine as profit
import pump_radar as radar


def _silent_sender(message:Any,*args:Any,**kwargs:Any):
    text=str(message or "")
    if text:
        print("PREMIUM-ONLY: PUMP/DUMP Telegram suppressed")
    return True


def run()->None:
    profile=profit.pump_profile()
    profile_live_eligible=bool(profile.get("live_allowed"))

    exchange=radar.get_exchange()
    impulse_state=impulse.update_market_impulse_state(exchange)
    scan_coins=impulse.scan_universe_from_state(
        impulse_state,
        normal_min_quote_volume=radar.MIN_24H_QUOTE_VOLUME,
        normal_max_scan_coins=radar.MAX_SCAN_COINS,
    )
    radar.get_exchange=lambda:exchange
    radar.get_scan_coins=lambda _exchange:list(scan_coins)

    # Shadow entries continue so Pump/Dump evidence keeps growing. They never
    # reach Telegram because the sender is forcibly replaced below.
    radar.MAX_NEW_SIGNALS_PER_RUN=1
    radar.MAX_OPEN_SIGNALS=1
    radar.has_open_same_symbol=lambda state,symbol:False
    radar.evaluate_portfolio_risk=capture.make_opposite_direction_evaluator(radar.evaluate_portfolio_risk)
    radar.send_telegram=_silent_sender

    print(
        "PREMIUM-ONLY / PUMP-DUMP SHADOW DATA | "
        "profile_live_eligible=",
        profile_live_eligible,
        "| cost-adjusted evidence:",
        profile,
    )
    radar.main()


if __name__=="__main__":
    run()
