"""Simplified live entry point for Market First.

All analysis/tracking layers stay active internally. Telegram shows only the
single final quality-filtered trade entry, trade results and the compact daily
outcome summary. PREP/EARLY/Big-Move observations remain internal evidence.
"""
from __future__ import annotations

import market_first_big_move_capture as big_move_capture
import market_first_daily_report as daily_report
import market_first_daily_report_origin_patch as daily_report_origin_patch
import market_first_entry_accelerator as entry_accelerator
import market_first_final_execution_gate as final_execution_gate
import market_first_live_complete_tracking as complete_tracking
import market_first_pre_entry_shadow as pre_entry_shadow
import market_first_profit_quality_v1 as profit_quality
import market_first_promotion_reason_patch as promotion_reason_patch
import market_first_reversal_capture_v2 as reversal_capture
import market_first_runner as runner
import market_first_shadow_edge as shadow_edge
import market_first_simple_mode as simple_mode
import market_first_swing_2h_tracking_fix as swing_tracking_fix
import market_first_tao_quality_bridge as tao_quality_bridge
import market_first_tao_quality_profit_patch as tao_profit_patch
import market_first_target_display as target_display
import market_first_target_overlay as target_overlay


def main() -> None:
    # Order matters. Profit Quality first installs the single-message/high-profit
    # gate. The TAO compatibility patch then permits only the strict volume-burst
    # -> pullback profile to use a 0.50x current-volume floor, and the bridge sits
    # outside that so a qualifying PREP can enter the normal trade pipeline.
    # Shadow Edge is installed after those layers so it can use their final plan
    # shape and historical ledgers. It may only relax the structural target floor
    # for exceptionally strong aligned plans when the measured background edge is
    # positive and real-signal conversion is abnormally low.
    # Final Execution Gate is installed last: it never promotes a setup, it only
    # vetoes a would-be final signal when continuation/flow evidence is too weak.
    # Daily report origin/BE patch is reporting-only and changes no live decision.
    daily_report_origin_patch.install()
    entry_accelerator.install()
    promotion_reason_patch.install()
    swing_tracking_fix.install()
    complete_tracking.install_complete_tracking()
    pre_entry_shadow.install(runner)
    reversal_capture.install(runner)
    target_overlay.install_target_overlay()
    simple_mode.install_simple_mode()
    target_display.install_target_display()
    big_move_capture.install()
    profit_quality.install()
    tao_profit_patch.install()
    tao_quality_bridge.install()
    shadow_edge.install()
    final_execution_gate.install()
    print("MARKET FIRST DAILY REPORT ORIGIN/BE:", daily_report_origin_patch.summary())
    print("MARKET FIRST ENTRY ACCELERATOR:", entry_accelerator.summary())
    print("MARKET FIRST PROMOTION REASONS:", promotion_reason_patch.summary())
    print("MARKET FIRST 2H SWING TRACKING FIX:", swing_tracking_fix.status())
    print("MARKET FIRST PRE-ENTRY SHADOW:", pre_entry_shadow.summary())
    print("MARKET FIRST REVERSAL CAPTURE:", reversal_capture.summary())
    print("MARKET FIRST TARGET OVERLAY:", target_overlay.summary())
    print("MARKET FIRST TARGET DISPLAY:", target_display.summary())
    print("MARKET FIRST SIMPLE MODE:", simple_mode.summary())
    print("MARKET FIRST PROFIT QUALITY:", profit_quality.summary())
    print("MARKET FIRST TAO PROFIT PATCH:", tao_profit_patch.status())
    print("MARKET FIRST TAO QUALITY BRIDGE:", tao_quality_bridge.summary())
    print("MARKET FIRST SHADOW EDGE:", shadow_edge.summary())
    print("MARKET FIRST FINAL EXECUTION GATE:", final_execution_gate.summary())
    try:
        runner.run()
    finally:
        print("MARKET FIRST BIG MOVE CAPTURE:", big_move_capture.finish())
        print("MARKET FIRST PROFIT QUALITY RUN:", profit_quality.finish())
        print("MARKET FIRST TAO QUALITY RUN:", tao_quality_bridge.summary())
        print("MARKET FIRST SHADOW EDGE RUN:", shadow_edge.finish())
        print("MARKET FIRST FINAL EXECUTION RUN:", final_execution_gate.finish())
    sent = daily_report.maybe_send(runner.bot, runner._send)
    if sent:
        print("GÜNLÜK ÖZET TELEGRAM'A GÖNDERİLDİ.")


if __name__ == "__main__":
    main()
