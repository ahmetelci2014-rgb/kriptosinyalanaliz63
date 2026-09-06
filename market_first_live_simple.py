"""Simplified live entry point for Market First.

All analysis/tracking layers stay active internally. Telegram shows only real
trade entries and trade results, while preparation/radar messages remain silent.
"""
from __future__ import annotations

import market_first_live_complete_tracking as complete_tracking
import market_first_runner as runner
import market_first_simple_mode as simple_mode


def main() -> None:
    complete_tracking.install_complete_tracking()
    simple_mode.install_simple_mode()
    print("MARKET FIRST SIMPLE MODE:", simple_mode.summary())
    runner.run()


if __name__ == "__main__":
    main()
