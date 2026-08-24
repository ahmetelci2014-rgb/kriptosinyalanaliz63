"""Premium live runner with crypto-only, reversal and early-breakout guards."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional


PRIORITY_STAGE_MAX_AGE_SECONDS = 20 * 60
PRIORITY_STAGE_MAX_SYMBOLS = 16


def _make_trade_only_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    """Allow only real Premium entry messages to reach Telegram.

    TP/SL/BE/expiry/status/recovery/diagnostic messages are still processed by
    their normal background ledger logic, but this outer delivery gate keeps
    Telegram focused on new trade entries only.
    """
    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        is_trade_entry = (
            "✅ İŞLEM GİRİŞİ — PREMIUM" in text
            or text.startswith("🚀 PREMIUM FUTURES")
        )
        if not is_trade_entry:
            first_line = text.splitlines()[0] if text else "EMPTY"
            print(
                "TELEGRAM TRADE-ONLY | işlem dışı mesaj sessiz bırakıldı:",
                first_line,
            )
            # Callers often use True only to avoid retrying an already-recorded
            # result. The underlying Telegram API is intentionally not called.
            return True
        return original(message, *args, **kwargs)

    wrapped._trade_only_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def _prioritize_movement_symbols(runner: Any, symbols: Any) -> list[str]:
    """Put fresh V2 ARMED/TRIGGER candidates at the front of the next scan.

    This changes scan order only. It does not lower any Premium score, market,
    risk, cost, entry-distance, order-flow or portfolio gate.
    """
    ordered = [str(symbol or "").upper() for symbol in (symbols or []) if symbol]
    if not ordered:
        return ordered

    allowed = set(ordered)
    now = int(time.time())
    best_by_symbol: Dict[str, Dict[str, Any]] = {}

    try:
        state = runner.movement_start_v2._state()
        open_rows = state.get("open") or {}
    except Exception:
        return ordered

    for record in open_rows.values():
        if not isinstance(record, dict):
            continue
        if record.get("first_resolution"):
            continue

        symbol = str(record.get("symbol") or "").upper()
        if symbol not in allowed:
            continue

        stage = str(
            record.get("best_stage")
            or record.get("initial_stage")
            or ""
        ).upper()
        if stage not in {"ARMED", "TRIGGER"}:
            continue

        updated_at = int(
            record.get("last_updated_at")
            or record.get("started_at")
            or 0
        )
        if updated_at <= 0 or now - updated_at > PRIORITY_STAGE_MAX_AGE_SECONDS:
            continue

        score = int(
            record.get("best_score")
            or record.get("initial_score")
            or 0
        )
        row = {
            "symbol": symbol,
            "stage": stage,
            "score": score,
            "updated_at": updated_at,
        }
        current = best_by_symbol.get(symbol)
        stage_rank = 2 if stage == "TRIGGER" else 1
        current_rank = (
            2 if current and current.get("stage") == "TRIGGER" else 1
        ) if current else 0
        if (
            current is None
            or stage_rank > current_rank
            or (
                stage_rank == current_rank
                and score > int(current.get("score") or 0)
            )
        ):
            best_by_symbol[symbol] = row

    ranked = sorted(
        best_by_symbol.values(),
        key=lambda row: (
            1 if row["stage"] == "TRIGGER" else 0,
            int(row["score"]),
            int(row["updated_at"]),
        ),
        reverse=True,
    )[:PRIORITY_STAGE_MAX_SYMBOLS]

    priority = [row["symbol"] for row in ranked]
    if not priority:
        return ordered

    priority_set = set(priority)
    combined = priority + [symbol for symbol in ordered if symbol not in priority_set]
    print(
        "EARLY FAST-SCAN önceliği:",
        ", ".join(
            f"{row['symbol']}:{row['stage']}:{row['score']}"
            for row in ranked
        ),
    )
    return combined


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
    original_scanner_factory = runner._make_all_coin_scanner
    original_market_status = runner.bot.get_market_direction_status

    market_status_cache: Dict[str, Any] = {"value": None}
    fast_state = {
        "sent": 0,
        "base_normal_cap": int(runner.bot.MAX_TRADE_SIGNALS_PER_RUN),
        "base_risk_cap": int(runner.bot.RISK_MODE_MAX_TRADE_SIGNALS),
    }

    def remembering_market_status(exchange: Any) -> Dict[str, Any]:
        result = original_market_status(exchange)
        market_status_cache["value"] = result
        return result

    runner.bot.get_market_direction_status = remembering_market_status

    def priority_scanner_factory(original: Callable[..., Any]) -> Callable[..., Any]:
        legacy = original_scanner_factory(original)

        def wrapped(exchange: Any):
            symbols = legacy(exchange)
            return _prioritize_movement_symbols(runner, symbols)

        return wrapped

    runner._make_all_coin_scanner = priority_scanner_factory

    def _try_fast_send(signal: Dict[str, Any], current_price: Any) -> bool:
        """Send an already-qualified Early Breakout immediately during scanning."""
        if str(signal.get("source") or "").upper() != early.SOURCE:
            return False

        direction = str(signal.get("direction") or "").upper()
        if direction == "LONG" and not runner.bot.ALLOW_LONG:
            return False
        if direction == "SHORT" and not runner.bot.ALLOW_SHORT:
            return False
        if direction not in {"LONG", "SHORT"}:
            return False

        market_status = market_status_cache.get("value")
        if not isinstance(market_status, dict):
            # Normal main() path populates this immediately before the scan.
            # If unavailable, fall back to the legacy batch pipeline.
            return False
        if not market_status.get(direction, True):
            return False

        risk_mode = runner.bot.risk_mode_active()
        run_cap = (
            fast_state["base_risk_cap"]
            if risk_mode
            else fast_state["base_normal_cap"]
        )
        if int(fast_state["sent"]) >= int(run_cap):
            return False

        risky_open, _, _ = runner.bot.count_open_signal_risk()
        if risky_open >= runner.bot.MAX_OPEN_SIGNALS:
            return False

        if runner.bot.is_duplicate(signal, radar=False):
            return False

        valid, reason = runner.bot.is_entry_still_valid(signal, current_price)
        if not valid:
            print(
                signal.get("symbol"),
                "EARLY FAST-SEND son kontrol elendi:",
                reason,
            )
            return False

        portfolio_risk = runner.bot.evaluate_portfolio_risk(
            symbol=signal["symbol"],
            direction=signal["direction"],
            source_bot="MAIN_MTF",
        )
        signal["portfolio_risk"] = portfolio_risk
        if portfolio_risk.get("hard_block", False):
            print(
                signal.get("symbol"),
                "EARLY FAST-SEND portfolio block:",
                portfolio_risk.get("block_reason"),
            )
            return False

        entry_price = runner.bot.safe_float(signal.get("entry"))
        tp1_price = runner.bot.safe_float(signal.get("tp1"))
        live_price = runner.bot.safe_float(current_price)
        entry_distance = None
        tp1_progress = None

        if entry_price is not None and entry_price > 0 and live_price is not None:
            entry_distance = abs(live_price - entry_price) / entry_price * 100.0
            if (
                tp1_price is not None
                and direction == "LONG"
                and tp1_price > entry_price
            ):
                tp1_progress = (
                    (live_price - entry_price)
                    / (tp1_price - entry_price)
                    * 100.0
                )
            elif (
                tp1_price is not None
                and direction == "SHORT"
                and tp1_price < entry_price
            ):
                tp1_progress = (
                    (entry_price - live_price)
                    / (entry_price - tp1_price)
                    * 100.0
                )

        signal["sent_price"] = live_price
        signal["entry_distance_at_send_percent"] = (
            round(entry_distance, 4) if entry_distance is not None else None
        )
        signal["tp1_progress_at_send_percent"] = (
            round(tp1_progress, 4) if tp1_progress is not None else None
        )
        signal["market_guard_long_allowed"] = market_status.get("LONG")
        signal["market_guard_short_allowed"] = market_status.get("SHORT")
        signal["market_guard_reason"] = market_status.get("reason")

        message = runner.bot.build_short_trade_message(
            signal=signal,
            current_price=live_price,
            portfolio_risk=portfolio_risk,
        )
        if not runner.bot.send_telegram(message):
            return False

        runner.bot.save_open_signal(signal)
        runner.bot.mark_sent(signal, radar=False)
        runner.bot.update_performance(
            signal["symbol"],
            "OPENED",
            direction=signal["direction"],
            source=signal.get("source"),
            entry=signal.get("entry"),
            score=signal.get("score"),
        )

        fast_state["sent"] = int(fast_state["sent"]) + 1
        # Preserve the existing per-run signal cap. A fast delivery consumes one
        # of the same slots that the legacy end-of-scan batch would have used.
        runner.bot.MAX_TRADE_SIGNALS_PER_RUN = max(
            0,
            int(fast_state["base_normal_cap"]) - int(fast_state["sent"]),
        )
        runner.bot.RISK_MODE_MAX_TRADE_SIGNALS = max(
            0,
            int(fast_state["base_risk_cap"]) - int(fast_state["sent"]),
        )
        print(
            "PREMIUM EARLY FAST-SEND:",
            signal.get("symbol"),
            signal.get("direction"),
            "scan tamamlanması beklenmeden Telegram'a gönderildi.",
        )
        return True

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
                if _try_fast_send(promoted, current_price):
                    # Already persisted and delivered. Do not add it to the
                    # end-of-scan candidate batch a second time.
                    return None
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

    # Telegram is intentionally signal-only. Background TP/SL/BE/expiry and
    # diagnosis code continues to run and persist to JSON/ledger.
    if not getattr(premium_profit_runner.bot.send_telegram, "_trade_only_wrapped", False):
        premium_profit_runner.bot.send_telegram = _make_trade_only_sender(
            premium_profit_runner.bot.send_telegram
        )

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
    print(
        "Telegram modu: YALNIZ YENİ İŞLEM GİRİŞİ | TP/SL/BE/sonuç/teşhis sessiz ledger",
    )
    print(
        "Early Fast-Send: önceki tur ARMED/TRIGGER öncelikli + uygun aday tarama bitmeden gönderilir",
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
