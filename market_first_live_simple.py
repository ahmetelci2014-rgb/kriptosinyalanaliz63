"""Balanced live entry point for Market First V6.

V6 keeps the full analysis/tracking engine, but removes the stack of late-stage
survival/recovery/selective vetoes that was suppressing almost every internally
promoted opportunity. Live admission is intentionally simple again:

1) the existing analysis/entry engine finds the setup,
2) Profit Quality checks trade quality and risk/reward,
3) Final Execution checks the last micro/flow execution conditions,
4) the Balanced Core Guard enforces final send invariants,
5) Profit Lock manages already-open trade protection/tracking.

The removed layers are not deleted from the repository. Their historical state
and reports remain available for diagnostics, so this rollback is reversible.
"""
from __future__ import annotations

import market_first_background_live_bridge as background_live_bridge
import market_first_balanced_core_guard as balanced_core_guard
import market_first_big_move_capture as big_move_capture
import market_first_daily_report as daily_report
import market_first_daily_report_origin_patch as daily_report_origin_patch
import market_first_entry_accelerator as entry_accelerator
import market_first_entry_plan_context_patch as entry_plan_context_patch
import market_first_final_execution_gate as final_execution_gate
import market_first_selective_pre_signal as selective_pre_signal
import market_first_live_complete_tracking as complete_tracking
import market_first_pre_entry_shadow as pre_entry_shadow
import market_first_profit_lock as profit_lock
import market_first_profit_quality_v1 as profit_quality
import market_first_promotion_reason_patch as promotion_reason_patch
import market_first_reversal_capture_v2 as reversal_capture
import market_first_runner as runner
import market_first_simple_mode as simple_mode
import market_first_structure_fibo_contact as structure_fibo
import market_first_swing_2h_tracking_fix as swing_tracking_fix
import market_first_tao_quality_bridge as tao_quality_bridge
import market_first_tao_quality_profit_patch as tao_profit_patch
import market_first_target_display as target_display
import market_first_target_overlay as target_overlay


def main() -> None:
    # --- Analysis / entry generation ---
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

    # --- V6 live admission core ---
    # Keep one quality gate and one final execution gate. Do NOT install the old
    # Recovery/Survival/Selective/Candidate veto stack here. Those modules remain
    # in the repo for diagnostics and can be re-enabled if data later supports it.
    profit_quality.install()
    tao_profit_patch.install()
    tao_quality_bridge.install()
    final_execution_gate.install()
    selective_pre_signal.install()

    # Preserve original plan context for reports and trade tracking.
    entry_plan_context_patch.install()
    profit_lock.install()

    # Profit Quality owns final trade text; attach the FIB model label afterwards.
    structure_fibo.install_presentation()

    # This is deliberately installed last. The historical fast-entry path calls
    # runner._send_trade from inside the candidate scan, before outer wrappers
    # have returned. The final guard prevents that call from bypassing Profit
    # Quality / Final Execution while still letting the candidate continue
    # through the ordinary pipeline. It also protects minimum-floor stops from
    # being sent without a fresh micro trigger.
    balanced_core_guard.install()

    print("MARKET FIRST V6 MODE: BALANCED CORE LIVE")
    print("MARKET FIRST DAILY REPORT ORIGIN/BE:", daily_report_origin_patch.summary())
    print("MARKET FIRST ENTRY ACCELERATOR:", entry_accelerator.summary())
    print("MARKET FIRST ENTRY PLAN CONTEXT:", entry_plan_context_patch.summary())
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
    print("MARKET FIRST FINAL EXECUTION GATE:", final_execution_gate.summary())
    print("MARKET FIRST SELECTIVE PRE-SIGNAL:", selective_pre_signal.summary())
    print("MARKET FIRST V6 CORE GUARD:", balanced_core_guard.summary())
    print("MARKET FIRST PROFIT LOCK:", profit_lock.summary())

    try:
        runner.run()
    finally:
        print("MARKET FIRST BIG MOVE CAPTURE:", big_move_capture.finish())
        print("MARKET FIRST PROFIT QUALITY RUN:", profit_quality.finish())
        print("MARKET FIRST TAO QUALITY RUN:", tao_quality_bridge.summary())
        print("MARKET FIRST FINAL EXECUTION RUN:", final_execution_gate.finish())
        print("MARKET FIRST SELECTIVE PRE-SIGNAL RUN:", selective_pre_signal.summary())
        print("MARKET FIRST V6 CORE GUARD RUN:", balanced_core_guard.summary())
        print("MARKET FIRST BACKGROUND LIVE BRIDGE RUN:", background_live_bridge.finish())
        print("MARKET FIRST STRUCTURE FIBO RUN:", structure_fibo.finish())
        print("MARKET FIRST PROFIT LOCK RUN:", profit_lock.summary())

    sent = daily_report.maybe_send(runner.bot, runner._send)
    if sent:
        print("GÜNLÜK ÖZET TELEGRAM'A GÖNDERİLDİ.")


if __name__ == "__main__":
    main()
