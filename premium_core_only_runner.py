"""Premium Core-Only live runner.

Purpose
-------
Stop experimental entry routes from consuming real-money live slots while the
canonical 15M Premium core is re-measured in isolation.

Only ``15M_ENTRY`` may pass the final live admission gate. Big Move, Early
Breakout, Regime Transition, Trend Continuation and young/new-coin routes may
still exist in the repository for research/shadow use, but this runner does not
install their live wrappers and rejects any non-core source that reaches the
base Premium pipeline.

This module does not place exchange orders. Telegram remains entry-only.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

VERSION = "PREMIUM_CORE_ONLY_LIVE_V1_2026_08_26"
LIVE_SOURCE_ALLOWLIST = frozenset({"15M_ENTRY"})


def _signal_source(signal: Dict[str, Any]) -> str:
    return str((signal or {}).get("source") or "").strip().upper()


def _install_core_only_source_gate(runner: Any) -> None:
    """Wrap Premium's final admission factory with a strict source allowlist.

    The check happens before the normal profit/confirmation/global-quality
    logic. Therefore an experimental source cannot use a direct-entry shortcut
    to bypass the core-only policy.
    """
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

    # Preserve the existing ledger/backfill and fresh market-context safeguards.
    tracking_backfill.install(base_runner.bot)

    # Narrow live safety patch: verified crypto-only universe + pre-signal
    # no-chase geometry. It creates no new route and never opens an exchange order.
    core_entry_safety.install(base_runner)

    refresh = outlook_refresh.ensure_fresh()
    print(
        "Core-only Market Outlook:",
        refresh.get("reason"),
        "| fresh=",
        refresh.get("ok"),
        "| refreshed=",
        refresh.get("refreshed"),
        "| age_s=",
        refresh.get("age_seconds"),
    )

    # Telegram should remain quiet for TP/SL/BE/status messages; only a new
    # Premium entry is delivered. This wrapper does not create new routes.
    if not getattr(base_runner.bot.send_telegram, "_trade_only_wrapped", False):
        base_runner.bot.send_telegram = legacy_helpers._make_trade_only_sender(
            base_runner.bot.send_telegram
        )

    # Keep the proven global quality/late-entry protections for the core route.
    global_guard.install(base_runner.bot)

    # Secondary hard isolation: even if the base runner internally produces a
    # continuation/young/new signal, final live admission accepts 15M_ENTRY only.
    _install_core_only_source_gate(base_runner)

    # Refresh source truth before the scan so the audit reflects the exact
    # ledger state used for this isolation period.
    try:
        source_report.attach_to_profit_report(
            base_runner.bot.TRADE_LEDGER_FILE,
            base_runner.profit.REPORT_FILE,
        )
    except Exception as exc:
        print("Core-only pre-scan source report error:", exc)

    print(
        "PREMIUM CORE-ONLY LIVE:",
        VERSION,
        "| live source allowlist=",
        ",".join(sorted(LIVE_SOURCE_ALLOWLIST)),
        "| crypto-only=ON | pre-signal-no-chase=ON",
    )
    print(
        "Experimental live routes are quarantined: BIG_MOVE / EARLY_BREAKOUT / "
        "REGIME_TRANSITION / TREND_CONTINUATION / YOUNG_NEW"
    )
    print(
        "Movement Start and Market Structure research remain shadow-only; "
        "no experimental route can send a trade from this runner."
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
                "Core-only source performance 24h:",
                breakdown.get("windows", {}).get("24h", {}),
            )
        except Exception as exc:
            print("Core-only post-scan source report error:", exc)


if __name__ == "__main__":
    run()
