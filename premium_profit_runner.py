"""Premium Profit Mode V3 - persistent confirmation + cost-aware live gate.

No exchange orders are opened. The runner only produces Telegram signals,
tracks them, and manages Smart Recovery notifications.
"""
from __future__ import annotations
from typing import Any, Callable

import live_entry_safety as safety
import opportunity_capture as capture
import premium_confirmation as confirmation
import profitability_engine as profit
import strategy
import main as bot
import smart_recovery as recovery


def _make_clear_signal_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        if (
            "MTF FUTURES SİNYALİ" in text
            and "✅ İŞLEM GİRİŞİ — PREMIUM" not in text
        ):
            text = (
                "✅ İŞLEM GİRİŞİ — PREMIUM V3\n"
                "Sabit aday + canlı fiyat teyidi + maliyet-sonrası geçmiş avantaj geçti.\n\n"
                + text
            )
        return original(text, *args, **kwargs)

    return wrapped


def _make_profit_gate(
    original: Callable[..., Any],
    gate: profit.PremiumGate,
    pending_gate: confirmation.PendingConfirmationGate,
) -> Callable[..., Any]:
    def wrapped(signal: dict, current_price: Any):
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
                f"PROFIT V3 {label}:",
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
        fresh = original(
            symbol,
            df15m,
            df1h,
            df4h,
            current_price,
        )

        if fresh is not None:
            return fresh

        fallback = pending_gate.fallback_signal(
            symbol=symbol,
            df15m=df15m,
            df1h=df1h,
            df4h=df4h,
            strategy_module=strategy,
        )

        if fallback is not None:
            print(
                "PROFIT V3 PENDING RECHECK:",
                fallback.get("symbol"),
                fallback.get("direction"),
                "anchor=",
                fallback.get("entry"),
            )

        return fallback

    return wrapped


def run() -> None:
    # Premium geçmiş performans profili şu anda yalnız 15M_ENTRY işlemlerinden
    # oluşuyor. 5M erken trade bu nedenle ayrı kaynak performansı kanıtlanana
    # kadar Premium runner içinde kapalı tutulur. Ana strategy/config dosyaları
    # değiştirilmez.
    strategy.ENABLE_5M_EARLY_TRADE = False

    # Burada MAX_TRADE_SIGNALS_PER_RUN, MAX_OPEN_SIGNALS veya
    # RISK_MODE_STOP_COUNT üzerine gizli override yapılmaz. Ana config.py
    # değerleri aynen kullanılır.
    gate = profit.PremiumGate(bot.TRADE_LEDGER_FILE)
    pending_gate = confirmation.PendingConfirmationGate(gate)

    # Bekleyen sabit aday, aynı 15M reversal şartı bir sonraki turda yeniden
    # oluşmasa bile her taramada tekrar ana zincire sokulur. Main market guard,
    # duplicate, portfolio risk ve son giriş kontrolünü yeniden uygular.
    bot.analyze_mtf_trade = _make_pending_analyzer(
        bot.analyze_mtf_trade,
        pending_gate,
    )

    bot.is_entry_still_valid = _make_profit_gate(
        bot.is_entry_still_valid,
        gate,
        pending_gate,
    )

    # Aynı coinde ters yön fırsatı, eski sinyal yüzünden tamamen kaybolmasın.
    # Aynı yön çakışması ve toplam/yön portföy risk limitleri korunur.
    bot.has_open_same_symbol = lambda symbol: False
    bot.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        bot.evaluate_portfolio_risk
    )

    bot.send_telegram = safety.make_entry_safety_sender(bot.send_telegram)
    bot.send_telegram = _make_clear_signal_sender(bot.send_telegram)

    print(
        "PROFIT MODE V3 / PREMIUM | 15M | sabit aday + aktif 5dk yeniden teyit | "
        "5M erken trade kapalı"
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

    # Açık Premium işlemler için tek DCA1 fırsatını ana TP/SL motorundan
    # önce kontrol eder. Emir açmaz; yalnız Telegram uyarısı ve bağımsız
    # recovery sonucu üretir.
    recovery.run(bot)

    bot.main()

    changed = profit.enrich_premium(bot.TRADE_LEDGER_FILE)
    report = profit.report()
    print("Profit V3 ledger cost enrichment:", changed)
    print("Premium LONG edge:", report["premium"]["long"])
    print("Premium SHORT edge:", report["premium"]["short"])
    print("Premium teyit bekleyen aday (run sonu):", pending_gate.pending_count())


if __name__ == "__main__":
    run()
