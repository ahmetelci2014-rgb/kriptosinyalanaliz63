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


def _install_big_move(runner: Any, big: Any, false_positive_shadow: Any = None) -> None:
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

            # Prospective audit sees every Big Move PROMOTE candidate before a
            # later legacy/portfolio/slot decision can hide a false positive.
            if false_positive_shadow is not None:
                try:
                    false_positive_shadow.observe(promoted)
                except Exception as exc:
                    print(symbol, "Big Move false-positive shadow kayıt hatası:", exc)

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
    import big_move_false_positive_shadow as false_positive_shadow
    import premium_big_move_live as big
    import premium_market_outlook_refresh as outlook_refresh
    import premium_profit_runner as base_runner
    import premium_quality_profit_runner as quality_runner
    import source_performance_report as source_report
    import tracking_backfill

    refresh = outlook_refresh.ensure_fresh()
    print(
        "Premium inline Market Outlook:",
        refresh.get("reason"),
        "| fresh=",
        refresh.get("ok"),
        "| refreshed=",
        refresh.get("refreshed"),
        "| age_s=",
        refresh.get("age_seconds"),
    )

    tracking_backfill.install(base_runner.bot)

    # Refresh the canonical source + direction truth BEFORE any new live entry
    # is evaluated. This lets the global quality guard use the latest closed
    # trade ledger immediately instead of waiting until the next workflow run.
    try:
        pre_scan_breakdown = source_report.attach_to_profit_report(
            base_runner.bot.TRADE_LEDGER_FILE,
            base_runner.profit.REPORT_FILE,
        )
        print(
            "Pre-scan source performance truth:",
            source_report.VERSION,
            "| 7d=",
            pre_scan_breakdown.get("windows", {}).get("7d", {}),
        )
    except Exception as exc:
        # Fail-open here: the global guard independently refuses to trust a
        # missing/stale report, while the normal strategy continues safely.
        print("Pre-scan kaynak performans raporu hatası:", exc)

    false_positive_shadow.begin()
    try:
        false_positive_shadow.update_outcomes()
    except Exception as exc:
        print("Big Move false-positive shadow güncelleme hatası:", exc)

    big.begin()
    _install_big_move(base_runner, big, false_positive_shadow)

    print(
        "Premium Big Move Start:",
        big.VERSION,
        "| Movement Start + 1H/4H kanal kırılımı + momentum + ATR anti-chase CANLI",
    )
    print(
        "Big Move önceliği: mevcut Regime/Early/Premium yolları kaldırılmadı; büyük hareket başlangıcı önce aday olabilir",
    )
    print(
        "Big Move false-positive shadow:",
        false_positive_shadow.VERSION,
        "| tüm PROMOTE adaylar 24s prospectif takip",
    )

    try:
        quality_runner.run()
    finally:
        try:
            print("Premium Big Move özet:", big.finish())
        except Exception as exc:
            print("Premium Big Move state kaydetme hatası:", exc)
        try:
            print(
                "Big Move false-positive özet:",
                false_positive_shadow.finish(),
            )
        except Exception as exc:
            print("Big Move false-positive state kaydetme hatası:", exc)
        try:
            breakdown = source_report.attach_to_profit_report(
                base_runner.bot.TRADE_LEDGER_FILE,
                base_runner.profit.REPORT_FILE,
            )
            print(
                "Kaynak bazlı 24s performans:",
                breakdown.get("windows", {}).get("24h", {}),
            )
        except Exception as exc:
            print("Kaynak bazlı performans raporu hatası:", exc)


if __name__ == "__main__":
    run()
