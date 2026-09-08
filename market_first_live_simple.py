"""Simplified live entry point for Market First.

All analysis/tracking layers stay active internally. Telegram shows selective
early-entry opportunities, real trade entries/results and one compact daily
outcome summary. Technical target guidance is attached without changing trade
eligibility or live safety guards.
"""
from __future__ import annotations

import market_first_daily_report as daily_report
import market_first_entry_accelerator as entry_accelerator
import market_first_live_complete_tracking as complete_tracking
import market_first_reversal_capture_v2 as reversal_capture
import market_first_runner as runner
import market_first_simple_mode as simple_mode
import market_first_target_display as target_display
import market_first_target_overlay as target_overlay


def main() -> None:
    # Install the entry accelerator first, then the ordinary tracking/safety
    # stack. Reversal Capture sits outside that stack so recent DEAD alerts stay
    # in deep-scan priority while every final send guard remains unchanged.
    entry_accelerator.install()
    complete_tracking.install_complete_tracking()
    reversal_capture.install(runner)
    target_overlay.install_target_overlay()
    simple_mode.install_simple_mode()
    target_display.install_target_display()
    print("MARKET FIRST ENTRY ACCELERATOR:", entry_accelerator.summary())
    print("MARKET FIRST REVERSAL CAPTURE:", reversal_capture.summary())
    print("MARKET FIRST TARGET OVERLAY:", target_overlay.summary())
    print("MARKET FIRST TARGET DISPLAY:", target_display.summary())
    print("MARKET FIRST SIMPLE MODE:", simple_mode.summary())
    runner.run()
    sent = daily_report.maybe_send(runner.bot, runner._send)
    if sent:
        print("GÜNLÜK ÖZET TELEGRAM'A GÖNDERİLDİ.")


if __name__ == "__main__":
    main()
