"""Premium live runner with crypto-only, reversal and early-breakout guards."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


def _latest_flow_snapshot(runner: Any, symbol: str, direction: str) -> Optional[Dict[str, Any]]:
    """Reuse the V3 snapshot created moments earlier by the shadow observer."""
    try:
        state = runner.movement_start_v3._state()
        rows = state.get("snapshots") or []
        now = int(time.time())
        for row in reversed(rows[-40:]):
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").upper() != str(symbol or "").upper():
                continue
            if str(row.get("direction") or "").upper() != str(direction or "").upper():
                continue
            at = int(row.get("at") or 0)
            if at > 0 and now - at <= 90:
                return row
    except Exception:
        pass
    return None


def _install_early_breakout(runner: Any, early: Any) -> None:
    """Attach the early route without changing the legacy strategy functions."""
    original_5m_factory = runner._make_5m_start_observer
    original_profit_factory = runner._make_profit_gate

    def early_5m_factory(original: Callable[..., Any]) -> Callable[..., Any]:
        legacy = original_5m_factory(original)

        def wrapped(
            symbol: str,
            df5m: Any,
            df15m: Any,
            df1h: Any,
            df4h: Any,
            current_price: Any = None,
        ) -> Any:
            legacy_signal = legacy(
                symbol,
                df5m,
                df15m,
                df1h,
                df4h,
                current_price,
            )
            if isinstance(legacy_signal, dict) and str(legacy_signal.get("signal_class") or "").upper() == "TRADE":
                return legacy_signal

            try:
                base_result = runner.movement_start_v2.analyze(
                    symbol,
                    df5m,
                    df15m,
                    df1h,
                    df4h,
                    current_price,
                )
            except Exception as exc:
                print(symbol, "Early Breakout V2 analiz hatası:", exc)
                base_result = None

            if not isinstance(base_result, dict):
                return legacy_signal

            snapshot = _latest_flow_snapshot(
                runner,
                symbol,
                str(base_result.get("direction") or ""),
            )
            try:
                promoted = early.analyze_live_candidate(
                    symbol,
                    base_result,
                    current_price,
                    flow_snapshot=snapshot,
                    allow_extra_flow=True,
                )
            except Exception as exc:
                print(symbol, "Early Breakout canlı aday hatası:", exc)
                promoted = None

            if isinstance(promoted, dict):
                print(
                    "PREMIUM EARLY BREAKOUT:",
                    promoted.get("symbol"),
                    promoted.get("direction"),
                    promoted.get("early_breakout_stage"),
                    "base=",
                    promoted.get("early_breakout_base_score"),
                    "live=",
                    promoted.get("score"),
                    "flow=",
                    promoted.get("early_breakout_flow_score"),
                )
                return promoted
            return legacy_signal

        return wrapped

    def early_profit_factory(
        original: Callable[..., Any],
        gate: Any,
        pending_gate: Any,
    ) -> Callable[..., Any]:
        legacy = original_profit_factory(original, gate, pending_gate)

        def wrapped(signal: Dict[str, Any], current_price: Any):
            if early.strong_direct_allowed(
                signal,
                current_price,
                original,
                runner.profit,
            ):
                signal["premium_confirmation"] = {
                    "version": early.VERSION,
                    "status": "EARLY_BREAKOUT_DIRECT",
                    "confirmed_at": runner.bot.now_ts(),
                }
                signal["profit_mode_v2"] = {
                    "version": runner.profit.VERSION,
                    "decision": "PREMIUM_V4_EARLY_BREAKOUT_DIRECT",
                    "timing": {"mode": "EARLY_BREAKOUT_DIRECT"},
                    "evidence": {
                        "base_score": signal.get("early_breakout_base_score"),
                        "stage": signal.get("early_breakout_stage"),
                        "flow_score": signal.get("early_breakout_flow_score"),
                        "flow_confirmed": signal.get("early_breakout_flow_confirmed"),
                    },
                    "confirmation": signal.get("premium_confirmation"),
                }
                return True, "Premium Early Breakout güçlü direkt giriş"
            return legacy(signal, current_price)

        return wrapped

    runner._make_5m_start_observer = early_5m_factory
    runner._make_profit_gate = early_profit_factory
    runner.bot.is_duplicate = early.make_candidate_duplicate_guard(runner.bot.is_duplicate)
    runner.bot.build_short_trade_message = early.make_trade_message_builder(
        runner.bot.build_short_trade_message
    )


def run() -> None:
    # Patch before premium_profit_runner imports premium_all_coins so every live
    # universe build in this process sees crypto-only market metadata.
    import all_market_shadow as market_scan
    from crypto_universe_guard import install_crypto_only_guard

    install_crypto_only_guard(market_scan)

    import premium_early_breakout as early
    import premium_profit_runner
    from premium_reversal_capture import install as install_reversal_capture

    # Keep the legacy same-direction cooldown, but allow a strict opposite-side
    # Premium route after TP3 when fresh reversal structure is confirmed.
    install_reversal_capture(premium_profit_runner)

    early.begin()
    _install_early_breakout(premium_profit_runner, early)

    print(
        "Premium Early Breakout:",
        early.VERSION,
        "| Movement Start V2/V3 -> kontrollü canlı Premium köprüsü AKTİF",
    )

    try:
        premium_profit_runner.run()
    finally:
        try:
            print("Premium Early Breakout özet:", early.finish())
        except Exception as exc:
            print("Premium Early Breakout state kaydetme hatası:", exc)


if __name__ == "__main__":
    run()
