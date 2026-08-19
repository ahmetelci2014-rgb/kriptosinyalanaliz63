"""Pump/Dump Profit Mode V2.

Existing open signals are always tracked. New classic Pump/Dump entries are
allowed only when the historical REAL_SIGNAL ledger remains positive after the
conservative execution-cost model. No orders.
"""
from __future__ import annotations

import live_entry_safety as safety
import market_impulse_guard as impulse
import opportunity_capture as capture
import profitability_engine as profit
import pump_radar as radar


def run()->None:
    profile=profit.pump_profile()
    live=bool(profile.get("live_allowed"))

    exchange=radar.get_exchange()
    impulse_state=impulse.update_market_impulse_state(exchange)
    scan_coins=impulse.scan_universe_from_state(
        impulse_state,
        normal_min_quote_volume=radar.MIN_24H_QUOTE_VOLUME,
        normal_max_scan_coins=radar.MAX_SCAN_COINS,
    )
    radar.get_exchange=lambda:exchange
    radar.get_scan_coins=lambda _exchange:list(scan_coins)

    # Existing open Pump position tracking/result messages stay intact.
    radar.MAX_NEW_SIGNALS_PER_RUN=1 if live else 0
    radar.MAX_OPEN_SIGNALS=1
    radar.has_open_same_symbol=lambda state,symbol:False
    radar.evaluate_portfolio_risk=capture.make_opposite_direction_evaluator(radar.evaluate_portfolio_risk)
    radar.send_telegram=safety.make_entry_safety_sender(radar.send_telegram)

    print("PROFIT V2 / PUMP","LIVE" if live else "NEW ENTRIES OFF","| cost-adjusted evidence:",profile)
    radar.main()


if __name__=="__main__":
    run()
