"""Simplified live entry point for Market First.

All analysis/tracking layers stay active internally. Telegram shows only real
trade entries and trade results during the day, plus one compact daily outcome
summary near the end of the Türkiye trading day.
"""
from __future__ import annotations

import market_first_daily_report as daily_report
import market_first_live_complete_tracking as complete_tracking
import market_first_runner as runner
import market_first_simple_mode as simple_mode


def main() -> None:
    complete_tracking.install_complete_tracking()
    simple_mode.install_simple_mode()
    print("MARKET FIRST SIMPLE MODE:", simple_mode.summary())
    runner.run()
    sent = daily_report.maybe_send(runner.bot, runner._send)
    if sent:
        print("GÜNLÜK ÖZET TELEGRAM'A GÖNDERİLDİ.")


if __name__ == "__main__":
    main()
