"""Premium Profit Mode V4 - all-coins, adaptive history and cost-aware live gate.

No exchange orders are opened. Premium is the only live Telegram trade channel.
Every eligible OKX USDT perpetual is screened; mature, young and brand-new coins
use history-appropriate confirmation rules before a trade message can be sent.
"""
from __future__ import annotations
from typing import Any, Callable

import live_entry_safety as safety
import opportunity_capture as capture
import premium_confirmation as confirmation
import premium_all_coins as allcoins
import premium_continuation as continuation
import profitability_engine as profit
import strategy
import main as bot
import smart_recovery as recovery
import movement_start_shadow as movement_start
import movement_start_v2_shadow as movement_start_v2
import movement_start_v3_orderflow_shadow as movement_start_v3

EARLY_5M_SOURCE = "5M_RADAR"
EARLY_5M_LIVE_VERSION = "EARLY_5M_LIVE_V1_2026_08_26"


def _make_clear_signal_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        if (
            "MTF FUTURES SİNYALİ" in text
            and "✅ İŞLEM GİRİŞİ — PREMIUM" not in text
        ):
            text = (
                "✅ İŞLEM GİRİŞİ — PREMIUM V4\n"
                "Tüm-piyasa tarama + yaşa uygun teyit + maliyet kontrolü geçti.\n\n"
                + text
            )
        elif (
            (
                "PREMIUM GENÇ COİN FIRSATI" in text
                or "PREMIUM YENİ COİN FIRSATI" in text
                or "PREMIUM TREND DEVAM SİNYALİ" in text
            )
            and "✅ İŞLEM GİRİŞİ — PREMIUM" not in text
        ):
            text = "✅ İŞLEM GİRİŞİ — PREMIUM V4\n" + text
        return original(text, *args, **kwargs)

    return wrapped


def _direct_evidence_allows(gate: profit.PremiumGate, signal: dict) -> bool:
    source = str(signal.get("source") or "").upper()
    if source in {"YOUNG_COIN_ENTRY", "NEW_COIN_ENTRY"}:
        return True

    direction = str(signal.get("direction") or "").upper()
    evidence = gate.profiles.get(direction, {}) if isinstance(gate.profiles, dict) else {}
    return bool(evidence.get("live_allowed"))


def _early_5m_direct_allowed(
    signal: dict,
    current_price: Any,
    base_validator: Callable[..., Any],
) -> bool:
    """Admit only the already-existing, replay-proven 5M TRADE path.

    This does not create a new signal model. strategy.analyze_5m_radar has already
    enforced strict 4H+1H direction, 15M strength, 5M reversal/volume, risk and
    entry-zone conditions. Here we only avoid re-delaying that early signal
    through the 15M pending-confirmation queue while preserving the live
    validator, global-quality wrapper and execution-cost check.
    """
    if str(signal.get("source") or "").upper() != EARLY_5M_SOURCE:
        return False
    if str(signal.get("signal_class") or "").upper() != "TRADE":
        return False
    if not bool(strategy.ENABLE_5M_EARLY_TRADE):
        return False

    score = int(strategy.safe_float(signal.get("score"), 0.0))
    min_score = max(
        int(strategy.MIN_SCORE_TRADE),
        int(strategy.EARLY_TRADE_MIN_SCORE),
    )
    if score < min_score:
        return False

    ok, _ = base_validator(signal, current_price)
    if not ok:
        return False

    return bool(profit.cost_viability(signal).get("ok"))


def _make_profit_gate(
    original: Callable[..., Any],
    gate: profit.PremiumGate,
    pending_gate: confirmation.PendingConfirmationGate,
) -> Callable[..., Any]:
    def wrapped(signal: dict, current_price: Any):
        early_5m_direct = _early_5m_direct_allowed(
            signal,
            current_price,
            original,
        )
        direct_allowed = (
            early_5m_direct
            or allcoins.strong_direct_allowed(
                signal,
                current_price,
                original,
                profit,
            )
            or continuation.strong_direct_allowed(
                signal,
                current_price,
                original,
                profit,
            )
        )
        if _direct_evidence_allows(gate, signal) and direct_allowed:
            source = str(signal.get("source") or "").upper()
            if source == EARLY_5M_SOURCE:
                direct_status = "EARLY_5M_DIRECT"
                direct_version = EARLY_5M_LIVE_VERSION
            elif source == continuation.SOURCE:
                direct_status = "TREND_CONTINUATION_DIRECT"
                direct_version = continuation.VERSION
            else:
                direct_status = "STRONG_DIRECT"
                direct_version = allcoins.VERSION

            signal["premium_confirmation"] = {
                "version": direct_version,
                "status": direct_status,
                "confirmed_at": bot.now_ts(),
            }
            signal["profit_mode_v2"] = {
                "version": profit.VERSION,
                "decision": f"PREMIUM_V4_{direct_status}",
                "timing": {"mode": direct_status},
                "evidence": gate.profiles.get(
                    str(signal.get("direction") or "").upper(),
                    {},
                ),
                "confirmation": signal.get("premium_confirmation"),
            }
            print(
                "PREMIUM V4 DIREKT:",
                signal.get("symbol"),
                signal.get("direction"),
                signal.get("source"),
                "score=",
                signal.get("score"),
            )
            return True, "Premium V4 güçlü direkt giriş"

        ok, reason, result = pending_gate.evaluate(
            signal,
            current_price,
            original,
        )

        if result is not None:
            signal["profit_mode_v2"] = {
                "version": profit.VERSION,
                "decision": result.get("reason"),
                "timing": result.get("timing"),
                "evidence": result.get("evidence"),
                "confirmation": signal.get("premium_confirmation"),
            }

        if not ok:
            label = "BEKLE" if "bekleniyor" in str(reason).lower() else "RED"
            print(
                f"PROFIT V4 {label}:",
                signal.get("symbol"),
                signal.get("direction"),
                reason,
            )
            return False, reason

        return True, reason

    return wrapped


def _make_pending_analyzer(
    original: Callable[..., Any],
    pending_gate: confirmation.PendingConfirmationGate,
) -> Callable[..., Any]:
    def wrapped(
        symbol: str,
        df15m: Any,
        df1h: Any,
        df4h: Any,
        current_price: Any = None,
    ) -> Any:
        movement_event = movement_start.observe(
            symbol,
            df15m,
            df1h,
            df4h,
            current_price,
        )
        if movement_event is not None:
            record = movement_event.get("record") or {}
            result = movement_event.get("result") or {}
            print(
                "MOVEMENT START SHADOW:",
                symbol,
                result.get("direction"),
                result.get("stage"),
                "score=",
                result.get("score"),
                "entry=",
                record.get("entry"),
                "event=",
                movement_event.get("event"),
            )

        fresh = original(
            symbol,
            df15m,
            df1h,
            df4h,
            current_price,
        )

        if fresh is not None:
            return fresh

        trend_continue = continuation.analyze_continuation(
            symbol,
            df15m,
            df1h,
            df4h,
            current_price,
        )
        if trend_continue is not None:
            print(
                "PREMIUM V4 TREND DEVAM:",
                trend_continue.get("symbol"),
                trend_continue.get("direction"),
                "score=",
                trend_continue.get("score"),
                "vol5=",
                trend_continue.get("confirm_reason"),
            )
            return trend_continue

        young = allcoins.analyze_young_coin(
            symbol,
            df15m,
            df1h,
            df4h,
            current_price,
        )
        if young is not None:
            print(
                "PREMIUM V4 YOUNG/NEW:",
                young.get("symbol"),
                young.get("direction"),
                young.get("source"),
                "score=",
                young.get("score"),
            )
            return young

        fallback = pending_gate.fallback_signal(
            symbol=symbol,
            df15m=df15m,
            df1h=df1h,
            df4h=df4h,
            strategy_module=strategy,
        )

        if fallback is not None:
            print(
                "PROFIT V4 PENDING RECHECK:",
                fallback.get("symbol"),
                fallback.get("direction"),
                "anchor=",
                fallback.get("entry"),
            )

        return fallback

    return wrapped


def _make_5m_start_observer(original: Callable[..., Any]) -> Callable[..., Any]:
    """5M V2 yapısını ve yalnız güçlü adaylarda V3 OKX order-flow'u gölgede öğren."""
    def wrapped(
        symbol: str,
        df5m: Any,
        df15m: Any,
        df1h: Any,
        df4h: Any,
        current_price: Any = None,
    ) -> Any:
        # V3'ün order-flow sorgusunu sadece mevcut V2 price-structure adayı
        # tetikleyebilir. Böylece tüm piyasada iki ek REST çağrısı açmayız.
        base_result = movement_start_v2.analyze(
            symbol,
            df5m,
            df15m,
            df1h,
            df4h,
            current_price,
        )

        event = movement_start_v2.observe(
            symbol,
            df5m,
            df15m,
            df1h,
            df4h,
            current_price,
        )
        if event is not None:
            result = event.get("result") or {}
            record = event.get("record") or {}
            print(
                "MOVEMENT START V2 5M:",
                symbol,
                result.get("direction"),
                result.get("stage"),
                "score=",
                result.get("score"),
                "risk%=",
                record.get("risk_percent"),
                "event=",
                event.get("event"),
            )

        flow_event = movement_start_v3.observe(
            symbol,
            base_result,
            df5m,
            current_price,
        )
        if flow_event is not None:
            snapshot = flow_event.get("snapshot") or {}
            print(
                "MOVEMENT START V3 ORDERFLOW:",
                symbol,
                snapshot.get("direction"),
                snapshot.get("base_stage"),
                "base=",
                snapshot.get("base_score"),
                "flow=",
                snapshot.get("orderflow_score"),
                "confirmed=",
                snapshot.get("orderflow_confirmed"),
                "event=",
                flow_event.get("event"),
            )

        return original(
            symbol,
            df5m,
            df15m,
            df1h,
            df4h,
            current_price,
        )

    return wrapped


def _make_all_coin_scanner(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(exchange: Any):
        expanded = allcoins.build_scan_universe(
            exchange=exchange,
            priority_coins=bot.PRIORITY_COINS,
            min_quote_volume=bot.MIN_24H_QUOTE_VOLUME,
            max_scan_coins=bot.MAX_SCAN_COINS,
        )
        if expanded:
            return expanded
        return original(exchange)

    return wrapped


def run() -> None:
    # Replay proof (8 crypto majors / 7d) showed that the already-existing 5M
    # path delivered 36 trades, +18.55R and a 25-minute median lead versus 15M.
    # Do not silently disable it in runtime. Keep the configured no-chase limit.
    strategy.ENABLE_5M_EARLY_TRADE = True
    strategy.MAX_LATE_ENTRY_DISTANCE_PERCENT = 0.25

    gate = profit.PremiumGate(bot.TRADE_LEDGER_FILE)
    pending_gate = confirmation.PendingConfirmationGate(gate)

    movement_start.begin()
    movement_start_v2.begin()
    movement_start_v3.begin()

    bot.get_scan_coins = _make_all_coin_scanner(bot.get_scan_coins)
    bot.fetch_df = allcoins.make_adaptive_fetcher(bot.fetch_df)

    bot.analyze_mtf_trade = _make_pending_analyzer(
        bot.analyze_mtf_trade,
        pending_gate,
    )
    bot.analyze_5m_radar = _make_5m_start_observer(bot.analyze_5m_radar)

    bot.is_entry_still_valid = _make_profit_gate(
        bot.is_entry_still_valid,
        gate,
        pending_gate,
    )

    bot.has_open_same_symbol = lambda symbol: False
    bot.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        bot.evaluate_portfolio_risk
    )

    bot.send_telegram = safety.make_entry_safety_sender(bot.send_telegram)
    bot.send_telegram = _make_clear_signal_sender(bot.send_telegram)

    print(
        "PROFIT MODE V4 / PREMIUM ALL-COINS | "
        "tüm USDT perpetual + klasik MTF + kontrollü trend devam + adaptif genç/yeni"
    )
    print(
        "Premium 5M erken giriş:",
        "AKTİF",
        "| source=",
        EARLY_5M_SOURCE,
        "| pending bekleme: YOK | canlı kalite/maliyet kapıları: AKTİF",
    )
    print(
        "Movement Start Shadow V1:",
        movement_start.VERSION,
        "| canlı sinyal: KAPALI | öğrenme: AKTİF",
    )
    print(
        "Movement Start Shadow V2:",
        movement_start_v2.VERSION,
        "| 5M squeeze+sweep+internal break+R öğrenme | canlı sinyal: KAPALI",
    )
    print(
        "Movement Start Shadow V3:",
        movement_start_v3.VERSION,
        "| OKX trades+book order-flow | canlı sinyal: KAPALI | emir: YOK",
    )
    print(
        "Premium aday yakalama | 15M azami bölge uzaklığı:",
        strategy.MAX_LATE_ENTRY_DISTANCE_PERCENT,
        "% | canlı teyit kapıları korunuyor",
    )
    print(
        "Premium canlı limitler | tur:",
        bot.MAX_TRADE_SIGNALS_PER_RUN,
        "| riskli açık:",
        bot.MAX_OPEN_SIGNALS,
        "| risk modu stop:",
        bot.RISK_MODE_STOP_COUNT,
    )
    print(
        "Premium teyit bekleyen aday:",
        pending_gate.pending_count(),
    )

    recovery.run(bot)

    movement_summary = {}
    movement_v2_summary = {}
    movement_v3_summary = {}
    try:
        bot.main()
    finally:
        movement_summary = movement_start.finish()
        movement_v2_summary = movement_start_v2.finish()
        movement_v3_summary = movement_start_v3.finish()
        print("Movement Start Shadow V1 özet:", movement_summary)
        print("Movement Start Shadow V2 özet:", movement_v2_summary)
        print("Movement Start Shadow V3 özet:", movement_v3_summary)

    changed = profit.enrich_premium(bot.TRADE_LEDGER_FILE)
    report = profit.report()
    print("Profit V4 ledger cost enrichment:", changed)
    print("Premium LONG edge:", report["premium"]["long"])
    print("Premium SHORT edge:", report["premium"]["short"])
    print("Premium teyit bekleyen aday (run sonu):", pending_gate.pending_count())


if __name__ == "__main__":
    run()
