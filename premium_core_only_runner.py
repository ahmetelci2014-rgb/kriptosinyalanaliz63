"""Premium Core live runner: canonical 15M + replay-scoped 5M research gate.

Only two Premium sources may reach the live admission pipeline:
- ``15M_ENTRY``: canonical Premium confirmation route.
- ``5M_RADAR``: strict 5M early-trade route, kept available for analysis but
  with live directions disabled by default until execution-cost-adjusted edge
  is proven again.

The 5M route was originally validated on eight majors, but later ran across the
full all-coins scanner. Live evidence then deteriorated: the broader lifetime
sample had no durable edge after execution costs, and the latest 7-day sample
was also negative even though it consisted only of LONG trades. For capital
protection this runner therefore keeps the replay-proven symbol scope but fails
closed for BOTH 5M directions by default. 15M and all shadow/research logic stay
unchanged.

``PREMIUM_5M_LIVE_SYMBOLS`` and ``PREMIUM_5M_LIVE_DIRECTIONS`` may explicitly
override these defaults only after future no-lookahead/live evidence validates
an expansion. Big Move, Early Breakout, Regime Transition, Trend Continuation
and young/new coin routes remain quarantined from live capital. This module
never places exchange orders.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, FrozenSet

VERSION = "PREMIUM_CORE_EARLY_LIVE_V4_2026_08_28"
EARLY_SOURCE = "5M_RADAR"
LIVE_SOURCE_ALLOWLIST = frozenset({"15M_ENTRY", EARLY_SOURCE})

# Exact universe used by the replay that originally justified 5M activation.
# Keep this scope for research/revalidation; live direction admission is handled
# independently below.
REPLAY_PROVEN_5M_SYMBOLS: FrozenSet[str] = frozenset(
    {
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "XRPUSDT",
        "DOGEUSDT",
        "SUIUSDT",
        "TRXUSDT",
    }
)

# Capital-protection default: no 5M direction is live right now.
# Latest 7d 5M evidence was negative after costs even though all 10 trades were
# LONG; older/lifetime SHORT evidence was also materially negative. Re-enable a
# direction only after fresh no-lookahead/live validation proves positive edge.
DEFAULT_5M_LIVE_DIRECTIONS: FrozenSet[str] = frozenset()


def _signal_source(signal: Dict[str, Any]) -> str:
    return str((signal or {}).get("source") or "").strip().upper()


def _signal_symbol(signal: Dict[str, Any]) -> str:
    return str((signal or {}).get("symbol") or "").strip().upper()


def _signal_direction(signal: Dict[str, Any]) -> str:
    return str((signal or {}).get("direction") or "").strip().upper()


def _parse_env_set(name: str, default: FrozenSet[str]) -> FrozenSet[str]:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default

    parsed = frozenset(
        part.strip().upper()
        for part in raw.replace(";", ",").split(",")
        if part.strip()
    )
    return parsed or default


def _live_5m_symbols() -> FrozenSet[str]:
    """Return explicit override or the replay-proven research universe."""
    return _parse_env_set("PREMIUM_5M_LIVE_SYMBOLS", REPLAY_PROVEN_5M_SYMBOLS)


def _live_5m_directions() -> FrozenSet[str]:
    """Return explicit validated override or the fail-closed default set."""
    return _parse_env_set(
        "PREMIUM_5M_LIVE_DIRECTIONS",
        DEFAULT_5M_LIVE_DIRECTIONS,
    )


def _install_core_only_source_gate(runner: Any) -> None:
    """Wrap Premium's final admission factory with source and 5M scope gates."""
    original_factory = runner._make_profit_gate

    def core_factory(
        original: Callable[..., Any],
        gate: Any,
        pending_gate: Any,
    ) -> Callable[..., Any]:
        legacy = original_factory(original, gate, pending_gate)

        def wrapped(signal: Dict[str, Any], current_price: Any):
            source = _signal_source(signal)
            symbol = _signal_symbol(signal)
            direction = _signal_direction(signal)

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
                    symbol or signal.get("symbol"),
                    direction or signal.get("direction"),
                    source or "UNKNOWN",
                )
                return False, reason

            if source == EARLY_SOURCE:
                allowed_5m = _live_5m_symbols()
                if not symbol or symbol not in allowed_5m:
                    signal["core_only_live_gate"] = {
                        "version": VERSION,
                        "decision": "BLOCK_5M_UNVALIDATED_UNIVERSE",
                        "source": source,
                        "symbol": symbol or "UNKNOWN",
                        "direction": direction or "UNKNOWN",
                        "allowed_sources": sorted(LIVE_SOURCE_ALLOWLIST),
                        "allowed_5m_symbols": sorted(allowed_5m),
                        "allowed_5m_directions": sorted(_live_5m_directions()),
                    }
                    reason = f"CORE_5M_REPLAY_UNIVERSE_BLOCK:{symbol or 'UNKNOWN'}"
                    print(
                        "CORE 5M REPLAY UNIVERSE BLOCK:",
                        symbol or "UNKNOWN",
                        direction or "UNKNOWN",
                    )
                    return False, reason

                allowed_directions = _live_5m_directions()
                if not direction or direction not in allowed_directions:
                    signal["core_only_live_gate"] = {
                        "version": VERSION,
                        "decision": "BLOCK_5M_DIRECTION_QUARANTINE",
                        "source": source,
                        "symbol": symbol,
                        "direction": direction or "UNKNOWN",
                        "allowed_sources": sorted(LIVE_SOURCE_ALLOWLIST),
                        "allowed_5m_symbols": sorted(allowed_5m),
                        "allowed_5m_directions": sorted(allowed_directions),
                    }
                    reason = f"CORE_5M_DIRECTION_BLOCK:{direction or 'UNKNOWN'}"
                    print(
                        "CORE 5M DIRECTION BLOCK:",
                        symbol,
                        direction or "UNKNOWN",
                    )
                    return False, reason

            ok, reason = legacy(signal, current_price)
            signal["core_only_live_gate"] = {
                "version": VERSION,
                "decision": "ALLOW" if ok else "CORE_REJECTED_BY_EXISTING_GATES",
                "source": source,
                "symbol": symbol or "UNKNOWN",
                "direction": direction or "UNKNOWN",
                "allowed_sources": sorted(LIVE_SOURCE_ALLOWLIST),
                "allowed_5m_symbols": (
                    sorted(_live_5m_symbols()) if source == EARLY_SOURCE else None
                ),
                "allowed_5m_directions": (
                    sorted(_live_5m_directions()) if source == EARLY_SOURCE else None
                ),
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

    # Keep 5M recorded independently in the performance report so existing
    # evidence remains available while the route is revalidated in research.
    if EARLY_SOURCE not in source_report.DEFAULT_LIVE_SOURCES:
        source_report.DEFAULT_LIVE_SOURCES = tuple(source_report.DEFAULT_LIVE_SOURCES) + (
            EARLY_SOURCE,
        )

    # Preserve the stricter fast-route send-time geometry if 5M is explicitly
    # re-enabled after future validation.
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

    live_5m = sorted(_live_5m_symbols())
    live_5m_directions = sorted(_live_5m_directions())
    print(
        "PREMIUM CORE LIVE:",
        VERSION,
        "| live source allowlist=",
        ",".join(sorted(LIVE_SOURCE_ALLOWLIST)),
        "| 5M live=PAUSED_REVALIDATION | crypto-only=ON | send-time-no-chase=ON",
    )
    print(
        "5M replay research universe:",
        ",".join(live_5m),
        "| live directions=",
        ",".join(live_5m_directions) if live_5m_directions else "NONE",
        "| count=",
        len(live_5m),
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
