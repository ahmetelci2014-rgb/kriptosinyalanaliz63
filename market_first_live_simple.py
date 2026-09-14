"""Simplified live entry point for Market First.

All analysis/tracking layers stay active internally. Telegram shows only useful
live decisions: final trades/results, capital-protection instructions and a very
small number of strong candidates when survival mode permits them.
"""
from __future__ import annotations

import market_first_background_live_bridge as background_live_bridge
import market_first_big_move_capture as big_move_capture
import market_first_candidate_survival_patch as candidate_survival_patch
import market_first_candidate_visibility as candidate_visibility
import market_first_daily_report as daily_report
import market_first_daily_report_origin_patch as daily_report_origin_patch
import market_first_entry_accelerator as entry_accelerator
import market_first_final_execution_gate as final_execution_gate
import market_first_live_complete_tracking as complete_tracking
import market_first_pre_entry_shadow as pre_entry_shadow
import market_first_profit_lock as profit_lock
import market_first_profit_quality_v1 as profit_quality
import market_first_profit_survival_gate as profit_survival_gate
import market_first_profit_survival_v2 as profit_survival_v2
import market_first_promotion_reason_patch as promotion_reason_patch
import market_first_reversal_capture_v2 as reversal_capture
import market_first_runner as runner
import market_first_shadow_edge as shadow_edge
import market_first_simple_mode as simple_mode
import market_first_structure_fibo_contact as structure_fibo
import market_first_swing_2h_tracking_fix as swing_tracking_fix
import market_first_tao_quality_bridge as tao_quality_bridge
import market_first_tao_quality_profit_patch as tao_profit_patch
import market_first_target_display as target_display
import market_first_target_overlay as target_overlay


def main() -> None:
    # Order matters. Entry Accelerator installs the ordinary PREP->ENTRY path.
    # Background Live Bridge adds validated shadow-history promotion. Structure
    # Fibo then sees only the PREPs still not promoted and may convert a confirmed
    # 1H HL->HH/LH->LL + closed 5M 0.618-0.65 contact into ENTRY. None of these
    # bridges bypass the common downstream quality/execution/survival stack.
    daily_report_origin_patch.install()
    entry_accelerator.install()
    background_live_bridge.install()
    structure_fibo.install()
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

    # Profit Survival V2 patches the V1 health source before V1 installs its final
    # wrapper. Candidate visibility and FIB entries remain subordinate to it.
    profit_survival_v2.install()
    profit_survival_gate.install()
    candidate_survival_patch.install()
    candidate_visibility.install()
    profit_lock.install()

    # Profit Quality owns the final trade text, so add the FIB model label only
    # after all message-formatting layers are installed.
    structure_fibo.install_presentation()

    print("MARKET FIRST DAILY REPORT ORIGIN/BE:", daily_report_origin_patch.summary())
    print("MARKET FIRST ENTRY ACCELERATOR:", entry_accelerator.summary())
    print("MARKET FIRST BACKGROUND LIVE BRIDGE:", background_live_bridge.summary())
    print("MARKET FIRST STRUCTURE FIBO CONTACT:", structure_fibo.summary())
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
    print("MARKET FIRST PROFIT SURVIVAL V2:", profit_survival_v2.summary())
    print("MARKET FIRST PROFIT SURVIVAL GATE:", profit_survival_gate.summary())
    print("MARKET FIRST CANDIDATE SURVIVAL:", candidate_survival_patch.summary())
    print("MARKET FIRST CANDIDATE VISIBILITY:", candidate_visibility.summary())
    print("MARKET FIRST PROFIT LOCK:", profit_lock.summary())
    try:
        runner.run()
    finally:
        print("MARKET FIRST BIG MOVE CAPTURE:", big_move_capture.finish())
        print("MARKET FIRST PROFIT QUALITY RUN:", profit_quality.finish())
        print("MARKET FIRST TAO QUALITY RUN:", tao_quality_bridge.summary())
        print("MARKET FIRST SHADOW EDGE RUN:", shadow_edge.finish())
        print("MARKET FIRST FINAL EXECUTION RUN:", final_execution_gate.finish())
        print("MARKET FIRST PROFIT SURVIVAL RUN:", profit_survival_gate.finish())
        print("MARKET FIRST PROFIT SURVIVAL V2 RUN:", profit_survival_v2.finish())
        print("MARKET FIRST BACKGROUND LIVE BRIDGE RUN:", background_live_bridge.finish())
        print("MARKET FIRST STRUCTURE FIBO RUN:", structure_fibo.finish())
        print("MARKET FIRST CANDIDATE SURVIVAL RUN:", candidate_survival_patch.summary())
        print("MARKET FIRST CANDIDATE VISIBILITY RUN:", candidate_visibility.summary())
        print("MARKET FIRST PROFIT LOCK RUN:", profit_lock.summary())
    sent = daily_report.maybe_send(runner.bot, runner._send)
    if sent:
        print("GÜNLÜK ÖZET TELEGRAM'A GÖNDERİLDİ.")


if __name__ == "__main__":
    main()
