"""Premium Core live runner: canonical 15M + replay-validated 5M early entry.

Only two Premium sources may reach the live Telegram admission pipeline:
- ``15M_ENTRY``: canonical Premium confirmation route.
- ``5M_RADAR``: the existing strict 5M early-trade route, activated after
  no-lookahead replay evidence.

Big Move, Early Breakout, Regime Transition, Trend Continuation and young/new
coin routes remain quarantined from live capital. This module never places
exchange orders.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

VERSION = "PREMIUM_CORE_EARLY_LIVE_V2_2026_08_26"
EARLY_SOURCE = "5M_RADAR"
LIVE_SOURCE_ALLOWLIST = frozenset({"15M_ENTRY", EARLY_SOURCE})


def _signal_source(signal: Dict[str, Any]) -> str:
    return str((signal or {}).get("source") or "").strip().upper()


def _install_core_only_source_gate(runner: Any) -> None:
    """Wrap Premium's final admission factory with the strict two-source list."""
    original_factory = runner._make_profit_gate

    def core_factory(
        original: Callable[..., Any],
        gate: Any,
        pending_gate: Any,
    ) -> Callable[..., Any]:
        legacy = original_factory(original, gate, pending_gate)

        def wrapped(signal: Dict[str, Any], current_price: Any):
            source = _signal_source(signal)
            if source not in LIVE_SOURCE_ALLOWLIST:
                signal["core_only_live_gate"] = {
                    "version": VERSION,
                    "decision": "BLOCK",
                    "source": source or "UNKNOWN",
                    "allowed_sources": sorted(LIVE_SOURCE_ALLOWLIST),
                }
                reason = f"CORE_ONLY_SOURCE_BLOCK:{source or 'UNKNOWN'}"
                print(
                    "CORE ONLY LIVE BLOCK:",
                    signal.get("symbol"),
                    signal.get("direction"),
                    source or "UNKNOWN",
                )
                return False, reason

            ok, reason = legacy(signal, current_price)
            signal["core_only_live_gate"] = {
                "version": VERSION,
                "decision": "ALLOW" if ok else "CORE_REJECTED_BY_EXISTING_GATES",
                "source": source,
                "allowed_sources": sorted(LIVE_SOURCE_ALLOWLIST),
                "existing_gate_reason": reason,
            }
            return ok, reason

        return wrapped

    runner._make_profit_gate = core_factory


def run() -> None:
    import premium_core_entry_safety as core_entry_safety
    import premium_crypto_profit_runner as legacy_helpers
    import premium_global_quality_guard as global_guard
    import premium_market_outlook_refresh as outlook_refresh
    import premium_profit_runner as base_runner
    import source_performance_report as source_report
    import tracking_backfill

    tracking_backfill.install(base_runner.bot)

    # Record 5M_RADAR independently in the same source-performance report so
    # live results can quarantine it automatically if the real edge deteriorates.
    if EARLY_SOURCE not in source_report.DEFAULT_LIVE_SOURCES:
        source_report.DEFAULT_LIVE_SOURCES = tuple(source_report.DEFAULT_LIVE_SOURCES) + (
            EARLY_SOURCE,
        )

    # Early entries should be rejected sooner if delivery has already consumed
    # too much of TP1. Reuse the existing fast-route send-time geometry rule.
    global_guard.FAST_ROUTES.add(EARLY_SOURCE)

    # Preserve the existing ledger/backfill and crypto-only/no-chase safeguards.
    core_entry_safety.install(base_runner)

    refresh = outlook_refresh.ensure_fresh()
    print(
        "Core Market Outlook:",
        refresh.get("reason"),
        "| fresh=",
        refresh.get("ok"),
        "| refreshed=",
        refresh.get("refreshed"),
        "| age_s=",
        refresh.get("age_seconds"),
    )

    # Telegram stays entry-only: no TP/SL/BE/status noise from this live runner.
    if not getattr(base_runner.bot.send_telegram, "_trade_only_wrapped", False):
        base_runner.bot.send_telegram = legacy_helpers._make_trade_only_sender(
            base_runner.bot.send_telegram
        )

    global_guard.install(base_runner.bot)
    _install_core_only_source_gate(base_runner)

    try:
        source_report.attach_to_profit_report(
            base_runner.bot.TRADE_LEDGER_FILE,
            base_runner.profit.REPORT_FILE,
        )
    except Exception as exc:
        print("Core pre-scan source report error:", exc)

    print(
        "PREMIUM CORE LIVE:",
        VERSION,
        "| live source allowlist=",
        ",".join(sorted(LIVE_SOURCE_ALLOWLIST)),
        "| 5M early=ON | crypto-only=ON | send-time-no-chase=ON",
    )
    print(
        "Experimental live routes remain quarantined: BIG_MOVE / EARLY_BREAKOUT / "
        "REGIME_TRANSITION / TREND_CONTINUATION / YOUNG_NEW"
    )
    print(
        "Movement Start and Market Structure remain shadow/research only; "
        "they cannot send a live trade from this runner."
    )

    try:
        base_runner.run()
    finally:
        try:
            breakdown = source_report.attach_to_profit_report(
                base_runner.bot.TRADE_LEDGER_FILE,
                base_runner.profit.REPORT_FILE,
            )
            print(
                "Core source performance 24h:",
                breakdown.get("windows", {}).get("24h", {}),
            )
        except Exception as exc:
            print("Core post-scan source report error:", exc)


if __name__ == "__main__":
    run()
