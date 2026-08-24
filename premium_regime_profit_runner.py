"""Premium live runner with generic 4H/1H regime transition priority.

This keeps the current Premium V4 pipeline, TP3 reversal exception and Early
Breakout route, but inserts Premium Regime Transition *before* Early Breakout.
Therefore a strong multi-hour TREND_START/TREND_REVERSAL can claim the candidate
before a short-horizon breakout route sends a conflicting trade.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


def _install_regime_transition(runner: Any, regime: Any) -> None:
    """Insert the regime route ahead of the later-installed Early Breakout."""
    original_5m_factory = runner._make_5m_start_observer
    original_profit_factory = runner._make_profit_gate

    def regime_5m_factory(original: Callable[..., Any]) -> Callable[..., Any]:
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
                print(symbol, "Regime Transition V2 analiz hatası:", exc)
                base_result = None

            if not isinstance(base_result, dict):
                return legacy_signal

            try:
                promoted = regime.analyze_live_candidate(
                    symbol,
                    base_result,
                    df15m,
                    df1h,
                    df4h,
                    current_price,
                )
            except Exception as exc:
                print(symbol, "Regime Transition canlı aday hatası:", exc)
                promoted = None

            if not isinstance(promoted, dict):
                return legacy_signal

            # If an unforeseen legacy 5M trade exists, the larger-regime route
            # gets priority only when at least as strong. In normal Premium V4
            # the base 5M trade path is disabled, so this is defensive only.
            if (
                isinstance(legacy_signal, dict)
                and str(legacy_signal.get("signal_class") or "").upper() == "TRADE"
                and int(legacy_signal.get("score") or 0) > int(promoted.get("score") or 0)
            ):
                return legacy_signal

            print(
                "PREMIUM REGIME TRANSITION:",
                promoted.get("symbol"),
                promoted.get("direction"),
                promoted.get("regime_transition_mode"),
                "base=",
                promoted.get("regime_base_score"),
                "live=",
                promoted.get("score"),
                "origin_delay%=",
                promoted.get("regime_start_delay_percent"),
                "prior_move%=",
                promoted.get("regime_prior_move_percent"),
            )
            return promoted

        return wrapped

    def regime_profit_factory(
        original: Callable[..., Any],
        gate: Any,
        pending_gate: Any,
    ) -> Callable[..., Any]:
        legacy = original_profit_factory(original, gate, pending_gate)

        def wrapped(signal: Dict[str, Any], current_price: Any):
            if regime.strong_direct_allowed(
                signal,
                current_price,
                original,
                runner.profit,
            ):
                signal["premium_confirmation"] = {
                    "version": regime.VERSION,
                    "status": "REGIME_TRANSITION_DIRECT",
                    "confirmed_at": runner.bot.now_ts(),
                }
                signal["profit_mode_v2"] = {
                    "version": runner.profit.VERSION,
                    "decision": "PREMIUM_V4_REGIME_TRANSITION_DIRECT",
                    "timing": {
                        "mode": signal.get("regime_transition_mode")
                        or "REGIME_TRANSITION_DIRECT"
                    },
                    "evidence": {
                        "base_score": signal.get("regime_base_score"),
                        "direction_gap": signal.get("regime_direction_gap"),
                        "support_15m": signal.get("regime_support_15m"),
                        "support_1h": signal.get("regime_support_1h"),
                        "start_delay_percent": signal.get("regime_start_delay_percent"),
                        "prior_move_percent": signal.get("regime_prior_move_percent"),
                        "reversal_pullback_percent": signal.get(
                            "regime_reversal_pullback_percent"
                        ),
                    },
                    "confirmation": signal.get("premium_confirmation"),
                }
                print(
                    "PREMIUM V4 REGIME DIREKT:",
                    signal.get("symbol"),
                    signal.get("direction"),
                    signal.get("regime_transition_mode"),
                    "score=",
                    signal.get("score"),
                )
                return True, "Premium V4 güçlü rejim başlangıcı/dönüşü"

            return legacy(signal, current_price)

        return wrapped

    runner._make_5m_start_observer = regime_5m_factory
    runner._make_profit_gate = regime_profit_factory


def run() -> None:
    import all_market_shadow as market_scan
    from crypto_universe_guard import install_crypto_only_guard

    install_crypto_only_guard(market_scan)

    import premium_crypto_profit_runner as legacy_helpers
    import premium_early_breakout as early
    import premium_profit_runner
    import premium_regime_transition as regime
    import premium_reversal_capture as reversal

    # Keep the existing Telegram policy and all existing safety/ledger layers.
    if not getattr(premium_profit_runner.bot.send_telegram, "_trade_only_wrapped", False):
        premium_profit_runner.bot.send_telegram = legacy_helpers._make_trade_only_sender(
            premium_profit_runner.bot.send_telegram
        )

    # Existing narrow TP3 reversal exception remains intact.
    legacy_helpers._install_movement_reversal_probe(premium_profit_runner, reversal)
    reversal.install(premium_profit_runner)

    # IMPORTANT ORDER:
    # Regime Transition is installed first. Early Breakout is installed after it
    # and calls its legacy factory first. A strong regime trade therefore wins
    # before the short-horizon breakout route can claim the symbol.
    regime.begin()
    _install_regime_transition(premium_profit_runner, regime)

    early.begin()
    legacy_helpers._install_early_breakout(
        premium_profit_runner,
        early,
        reversal,
    )

    print(
        "Premium Regime Transition:",
        regime.VERSION,
        "| 4H/1H büyük hareket başlangıcı + genel LONG<->SHORT dönüşü CANLI",
    )
    print(
        "Regime önceliği: güçlü çok-saatlik yön varsa Early Breakout'tan önce değerlendirilir",
    )
    print(
        "Premium Early Breakout:",
        early.VERSION,
        "| rejim adayı yoksa mevcut kontrollü erken breakout yolu devam eder",
    )
    print(
        "Telegram modu: YALNIZ YENİ İŞLEM GİRİŞİ | TP/SL/BE/sonuç/teşhis sessiz ledger",
    )

    try:
        premium_profit_runner.run()
    finally:
        try:
            print("Premium Regime Transition özet:", regime.finish())
        except Exception as exc:
            print("Premium Regime Transition state kaydetme hatası:", exc)
        try:
            print("Premium Early Breakout özet:", early.finish())
        except Exception as exc:
            print("Premium Early Breakout state kaydetme hatası:", exc)


if __name__ == "__main__":
    run()
