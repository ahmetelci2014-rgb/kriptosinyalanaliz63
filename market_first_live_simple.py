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
import market_first_pre_entry_shadow as pre_entry_shadow
import market_first_promotion_reason_patch as promotion_reason_patch
import market_first_reversal_capture_v2 as reversal_capture
import market_first_runner as runner
import market_first_simple_mode as simple_mode
import market_first_swing_2h_tracking_fix as swing_tracking_fix
import market_first_target_display as target_display
import market_first_target_overlay as target_overlay


def main() -> None:
    # Install the entry accelerator first. Promotion-reason instrumentation then
    # installs the ordinary entry-plan wrapper. Corrected 2H swing tracking must
    # be installed before complete tracking loads/finalizes its swing ledger so
    # new swing episodes become entry-zone gated without rewriting legacy data.
    # PRE-ENTRY Shadow sits after tracking and only observes selected PREP setups;
    # it never changes the returned live decision. Reversal Capture stays outside
    # that stack so recent DEAD alerts and open shadow positions can both keep
    # scan priority.
    entry_accelerator.install()
    promotion_reason_patch.install()
    swing_tracking_fix.install()
    complete_tracking.install_complete_tracking()
    pre_entry_shadow.install(runner)
    reversal_capture.install(runner)
    target_overlay.install_target_overlay()
    simple_mode.install_simple_mode()
    target_display.install_target_display()
    print("MARKET FIRST ENTRY ACCELERATOR:", entry_accelerator.summary())
    print("MARKET FIRST PROMOTION REASONS:", promotion_reason_patch.summary())
    print("MARKET FIRST 2H SWING TRACKING FIX:", swing_tracking_fix.status())
    print("MARKET FIRST PRE-ENTRY SHADOW:", pre_entry_shadow.summary())
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
