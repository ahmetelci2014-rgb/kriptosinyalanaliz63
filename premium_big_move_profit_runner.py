"""Top-level Premium runner with live Big Move Start priority."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


def _latest_flow_snapshot(runner: Any, symbol: str, direction: str) -> Optional[Dict[str, Any]]:
    try:
        state = runner.movement_start_v3._state()
        rows = state.get("snapshots") or []
        now = int(time.time())
        for row in reversed(rows[-50:]):
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").upper() != str(symbol or "").upper():
                continue
            if str(row.get("direction") or "").upper() != str(direction or "").upper():
                continue
            at = int(row.get("at") or 0)
            if at > 0 and now - at <= 120:
                return row
    except Exception:
        pass
    return None


def _install_big_move(runner: Any, big: Any) -> None:
    """Insert Big Move before Regime/Early routes without removing old systems."""
    original_5m_factory = runner._make_5m_start_observer
    original_profit_factory = runner._make_profit_gate

    def big_5m_factory(original: Callable[..., Any]) -> Callable[..., Any]:
        legacy = original_5m_factory(original)

        def wrapped(
            symbol: str,
            df5m: Any,
            df15m: Any,
            df1h: Any,
            df4h: Any,
            current_price: Any = None,
        ) -> Any:
            # Run the existing observer first so the newest V2/V3 order-flow
            # snapshot is available for Big Move without extra REST requests.
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
                print(symbol, "Big Move V2 analiz hatası:", exc)
                return legacy_signal

            if not isinstance(base_result, dict):
                return legacy_signal

            snapshot = _latest_flow_snapshot(
                runner,
                symbol,
                str(base_result.get("direction") or ""),
            )
            try:
                promoted = big.analyze_live_candidate(
                    symbol,
                    base_result,
                    df15m,
                    df1h,
                    df4h,
                    current_price,
                    flow_snapshot=snapshot,
                )
            except Exception as exc:
                print(symbol, "Big Move canlı aday hatası:", exc)
                promoted = None

            if not isinstance(promoted, dict):
                return legacy_signal

            if (
                isinstance(legacy_signal, dict)
                and str(legacy_signal.get("signal_class") or "").upper() == "TRADE"
                and int(legacy_signal.get("score") or 0) > int(promoted.get("score") or 0)
            ):
                return legacy_signal

            print(
                "PREMIUM BIG MOVE START:",
                promoted.get("symbol"),
                promoted.get("direction"),
                "score=",
                promoted.get("score"),
                "base=",
                promoted.get("big_move_base_score"),
                "1H=",
                promoted.get("big_move_1h_points"),
                "4H=",
                promoted.get("big_move_4h_points"),
                "extensionATR=",
                promoted.get("big_move_break_extension_atr"),
                "origin%=",
                promoted.get("big_move_origin_move_percent"),
            )
            return promoted

        return wrapped

    def big_profit_factory(
        original: Callable[..., Any],
        gate: Any,
        pending_gate: Any,
    ) -> Callable[..., Any]:
        legacy = original_profit_factory(original, gate, pending_gate)

        def wrapped(signal: Dict[str, Any], current_price: Any):
            if big.strong_direct_allowed(
                signal,
                current_price,
                original,
                runner.profit,
            ):
                signal["premium_confirmation"] = {
                    "version": big.VERSION,
                    "status": "BIG_MOVE_DIRECT",
                    "confirmed_at": runner.bot.now_ts(),
                }
                signal["profit_mode_v2"] = {
                    "version": runner.profit.VERSION,
                    "decision": "PREMIUM_V4_BIG_MOVE_DIRECT",
                    "timing": {"mode": "BIG_MOVE_START"},
                    "evidence": {
                        "base_score": signal.get("big_move_base_score"),
                        "direction_gap": signal.get("big_move_direction_gap"),
                        "support_1h": signal.get("big_move_1h_points"),
                        "support_4h": signal.get("big_move_4h_points"),
                        "break_extension_atr": signal.get("big_move_break_extension_atr"),
                        "origin_move_percent": signal.get("big_move_origin_move_percent"),
                        "flow_score": signal.get("big_move_flow_score"),
                    },
                    "confirmation": signal.get("premium_confirmation"),
                }
                return True, "Premium Big Move güçlü direkt giriş"
            return legacy(signal, current_price)

        return wrapped

    runner._make_5m_start_observer = big_5m_factory
    runner._make_profit_gate = big_profit_factory
    runner.bot.build_short_trade_message = big.make_trade_message_builder(
        runner.bot.build_short_trade_message
    )


def run() -> None:
    import premium_big_move_live as big
    import premium_profit_runner as base_runner
    import premium_quality_profit_runner as quality_runner

    big.begin()
    _install_big_move(base_runner, big)

    print(
        "Premium Big Move Start:",
        big.VERSION,
        "| Movement Start + 1H/4H kanal kırılımı + momentum + ATR anti-chase CANLI",
    )
    print(
        "Big Move önceliği: mevcut Regime/Early/Premium yolları kaldırılmadı; büyük hareket başlangıcı önce aday olabilir",
    )

    try:
        quality_runner.run()
    finally:
        try:
            print("Premium Big Move özet:", big.finish())
        except Exception as exc:
            print("Premium Big Move state kaydetme hatası:", exc)


if __name__ == "__main__":
    run()
