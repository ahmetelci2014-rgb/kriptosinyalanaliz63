"""Premium Core live runner: proven 15M core + replay-validated 5M early entry.

The live Telegram channel stays deliberately narrow:
- ``15M_ENTRY`` remains the canonical Premium confirmation route.
- ``5M_RADAR`` is the existing strategy's strict early-trade route. It is
  enabled only here after no-lookahead replay evidence showed materially earlier
  entries while retaining positive R expectancy.
- Big Move, Early Breakout, Regime Transition, Trend Continuation and young/new
  routes remain quarantined from this runner.

This module sends Telegram entries only; it never places exchange orders.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict

VERSION = "PREMIUM_CORE_EARLY_LIVE_V2_2026_08_26"
EARLY_SOURCE = "5M_RADAR"
LIVE_SOURCE_ALLOWLIST = frozenset({"15M_ENTRY", EARLY_SOURCE})
EARLY_MIN_SCORE = 91
EARLY_MAX_LATE_DISTANCE_PERCENT = 0.25


def _signal_source(signal: Dict[str, Any]) -> str:
    return str((signal or {}).get("source") or "").strip().upper()


class _CoreStrategyProxy:
    """Keep the generic Premium runner conservative while Core enables 5M early.

    ``premium_profit_runner.run`` historically forced the 5M path off and relaxed
    the 15M late-distance threshold. Core owns the live policy now, so this proxy
    preserves the generic runner for other callers but pins the two Core runtime
    settings to the replay-tested values.
    """

    def __init__(self, module: Any) -> None:
        object.__setattr__(self, "_module", module)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_module"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        module = object.__getattribute__(self, "_module")
        if name == "ENABLE_5M_EARLY_TRADE":
            value = True
        elif name == "MAX_LATE_ENTRY_DISTANCE_PERCENT":
            value = EARLY_MAX_LATE_DISTANCE_PERCENT
        setattr(module, name, value)


def _install_core_runtime_strategy(base_runner: Any) -> None:
    strategy_obj = base_runner.strategy
    if not isinstance(strategy_obj, _CoreStrategyProxy):
        strategy_obj = _CoreStrategyProxy(strategy_obj)
        base_runner.strategy = strategy_obj
    strategy_obj.ENABLE_5M_EARLY_TRADE = True
    strategy_obj.MAX_LATE_ENTRY_DISTANCE_PERCENT = EARLY_MAX_LATE_DISTANCE_PERCENT


def _early_direct_gate(
    runner: Any,
    original: Callable[..., Any],
    gate: Any,
    signal: Dict[str, Any],
    current_price: Any,
):
    """Admit an already-strict 5M strategy signal without a second pending delay.

    The existing 5M analyzer already requires strict 4H+1H alignment, 15M
    context, 5M reversal/volume confirmation, score, risk and zone geometry.
    Here we still require the normal global/base validator, positive Premium
    direction evidence and execution-cost viability. This preserves the timing
    advantage measured in replay instead of waiting another 15-45 minutes.
    """
    if str(signal.get("signal_class") or "").upper() != "TRADE":
        return False, "CORE_5M_NOT_TRADE"

    try:
        score = int(float(signal.get("score") or 0))
    except Exception:
        score = 0
    if score < EARLY_MIN_SCORE:
        return False, f"CORE_5M_SCORE_LOW:{score}"

    direction = str(signal.get("direction") or "").upper()
    profiles = getattr(gate, "profiles", {})
    evidence = profiles.get(direction, {}) if isinstance(profiles, dict) else {}
    if isinstance(evidence, dict) and evidence and not bool(evidence.get("live_allowed")):
        return False, "CORE_5M_DIRECTION_EDGE_NOT_ALLOWED"

    ok, reason = original(signal, current_price)
    if not ok:
        return False, reason

    cost = runner.profit.cost_viability(signal)
    if not bool((cost or {}).get("ok")):
        return False, "CORE_5M_COST_NOT_VIABLE"

    now_value = int(time.time())
    signal["premium_confirmation"] = {
        "version": VERSION,
        "status": "CORE_5M_EARLY_DIRECT",
        "confirmed_at": now_value,
    }
    signal["profit_mode_v2"] = {
        "version": getattr(runner.profit, "VERSION", ""),
        "decision": "CORE_5M_EARLY_DIRECT",
        "timing": {"mode": "EARLY_5M_DIRECT"},
        "evidence": evidence,
        "cost": cost,
        "confirmation": signal.get("premium_confirmation"),
    }
    return True, "Premium Core 5M erken giriş"


def _install_core_only_source_gate(runner: Any) -> None:
    """Final live allowlist plus direct admission for the proven 5M route."""
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

            if source == EARLY_SOURCE:
                ok, reason = _early_direct_gate(
                    runner,
                    original,
                    gate,
                    signal,
                    current_price,
                )
            else:
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

    # Enable only the existing replay-tested 5M strategy path in this Core runner.
    _install_core_runtime_strategy(base_runner)

    # Make the early route visible as its own live performance source and apply
    # the stricter 30% send-time TP1-progress limit used for fast entries.
    if EARLY_SOURCE not in source_report.DEFAULT_LIVE_SOURCES:
        source_report.DEFAULT_LIVE_SOURCES = tuple(source_report.DEFAULT_LIVE_SOURCES) + (
            EARLY_SOURCE,
        )
    global_guard.FAST_ROUTES.add(EARLY_SOURCE)

    # Verified crypto-only universe + 15M pre-signal no-chase protection.
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

    if not getattr(base_runner.bot.send_telegram, "_trade_only_wrapped", False):
        base_runner.bot.send_telegram = legacy_helpers._make_trade_only_sender(
            base_runner.bot.send_telegram
        )

    # Existing market/direction/source/send-time quality protections still apply.
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
        "| live sources=",
        ",".join(sorted(LIVE_SOURCE_ALLOWLIST)),
        "| 5M early=ON | crypto-only=ON | pre-signal-no-chase=ON",
    )
    print(
        "Experimental live routes remain quarantined: BIG_MOVE / EARLY_BREAKOUT / "
        "REGIME_TRANSITION / TREND_CONTINUATION / YOUNG_NEW"
    )
    print(
        "Movement Start and Market Structure remain research/early-warning layers; "
        "they do not open an exchange order."
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
