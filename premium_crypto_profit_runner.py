"""Premium live runner with crypto-only, reversal and early-breakout guards."""
from __future__ import annotations

import json
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


def _install_movement_reversal_probe(runner: Any, reversal: Any) -> None:
    """Use current Movement Start V2 state when the removed Pump state is absent."""
    original_should_probe = reversal.should_probe_reversal

    def should_probe(bot: Any, symbol: str, **kwargs: Any) -> bool:
        try:
            if original_should_probe(bot, symbol, **kwargs):
                return True
        except Exception:
            pass
        context = reversal.recent_tp3_context(
            bot,
            symbol,
            now_ts=kwargs.get("now_ts"),
        )
        if not isinstance(context, dict):
            return False
        wanted = str(context.get("opposite_direction") or "").upper()
        if wanted not in {"LONG", "SHORT"}:
            return False
        try:
            with open(runner.movement_start_v2.STATE_FILE, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception:
            return False
        active = (state.get("open") or {}).get(
            f"{str(symbol or '').upper()}_{wanted}"
        )
        if not isinstance(active, dict):
            return False
        stage = str(active.get("best_stage") or active.get("initial_stage") or "").upper()
        score = int(active.get("best_score") or active.get("initial_score") or 0)
        updated = int(active.get("last_updated_at") or active.get("started_at") or 0)
        now = int(kwargs.get("now_ts") or time.time())
        return bool(
            stage in {"ARMED", "TRIGGER"}
            and score >= 76
            and updated > 0
            and now - updated <= 45 * 60
        )

    reversal.should_probe_reversal = should_probe


def _install_early_breakout(runner: Any, early: Any, reversal: Any) -> None:
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
                try:
                    recent_tp3 = reversal.recent_tp3_context(runner.bot, symbol)
                except Exception:
                    recent_tp3 = None
                if (
                    isinstance(recent_tp3, dict)
                    and str(promoted.get("direction") or "").upper()
                    != str(recent_tp3.get("opposite_direction") or "").upper()
                ):
                    print(symbol, "Early Breakout aynı yön TP3 cooldown nedeniyle reddedildi.")
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
    import all_market_shadow as market_scan
    from crypto_universe_guard import install_crypto_only_guard

    install_crypto_only_guard(market_scan)

    import premium_early_breakout as early
    import premium_profit_runner
    import premium_reversal_capture as reversal

    # The old Pump state was removed during repo cleanup. Reuse fresh V2
    # ARMED/TRIGGER state to open only the opposite-direction TP3 scan exception.
    _install_movement_reversal_probe(premium_profit_runner, reversal)
    reversal.install(premium_profit_runner)

    early.begin()
    _install_early_breakout(premium_profit_runner, early, reversal)

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
