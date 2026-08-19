"""Pump/Dump Profit Mode V1.

Uses the all-market impulse scan only to prioritise symbols for the proven
classic Pump/Dump engine. Raw impulse alerts and the unproven
TREND_CONTINUATION live path are intentionally absent. No exchange orders.
"""
from __future__ import annotations

import live_entry_safety as safety
import market_impulse_guard as impulse
import opportunity_capture as capture
import pump_radar as radar


def run() -> None:
    exchange = radar.get_exchange()
    impulse_state = impulse.update_market_impulse_state(exchange)

    scan_coins = impulse.scan_universe_from_state(
        impulse_state,
        normal_min_quote_volume=radar.MIN_24H_QUOTE_VOLUME,
        normal_max_scan_coins=radar.MAX_SCAN_COINS,
    )
    priority_count = len(impulse.priority_symbols(impulse_state))

    # Reuse one market snapshot. Impulse is a silent prioritiser, not a signal.
    radar.get_exchange = lambda: exchange
    radar.get_scan_coins = lambda _exchange: list(scan_coins)

    radar.MAX_NEW_SIGNALS_PER_RUN = 1
    radar.MAX_OPEN_SIGNALS = 1

    # Preserve reversal visibility without allowing same-direction duplication.
    radar.has_open_same_symbol = lambda state, symbol: False
    radar.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        radar.evaluate_portfolio_risk
    )
    radar.send_telegram = safety.make_entry_safety_sender(radar.send_telegram)

    print(
        "PROFIT MODE V1 / PUMP | klasik Pump/Dump gerçek giriş | "
        "impuls sadece sessiz öncelik | Trend Continuation canlı KAPALI | "
        f"derin tarama {len(scan_coins)} | öncelikli {priority_count} | max açık 1"
    )
    radar.main()


if __name__ == "__main__":
    run()
