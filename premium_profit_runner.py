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
        # Bu iki kaynak uzun EMA200 geçmişi olmadığı için kendi daha sıkı
        # skor/hacim/risk kapılarını kullanır; olgun 15M geçmiş metriğiyle
        # yanlış biçimde kanıtlanmış sayılmaz.
        return True

    # TREND_CONTINUATION dahil olgun kaynaklarda canlı yönün mevcut Premium
    # geçmiş edge'i hâlâ kanıtlı olmalıdır. Yeni yol bu korumayı atlamaz.
    direction = str(signal.get("direction") or "").upper()
    evidence = gate.profiles.get(direction, {}) if isinstance(gate.profiles, dict) else {}
    return bool(evidence.get("live_allowed"))


def _make_profit_gate(
    original: Callable[..., Any],
    gate: profit.PremiumGate,
    pending_gate: confirmation.PendingConfirmationGate,
) -> Callable[..., Any]:
    def wrapped(signal: dict, current_price: Any):
        # En güçlü olgun A+ adaylar, kontrollü trend-devam adayları ve daha sıkı
        # eşikten geçen genç/yeni coinler dar confirmation penceresinde kaybolmasın.
        direct_allowed = (
            allcoins.strong_direct_allowed(
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
            direct_status = (
                "TREND_CONTINUATION_DIRECT"
                if source == continuation.SOURCE
                else "STRONG_DIRECT"
            )
            signal["premium_confirmation"] = {
                "version": (
                    continuation.VERSION
                    if source == continuation.SOURCE
                    else allcoins.VERSION
                ),
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
        # Mevcut kanıtlanmış Premium MTF kalıbı her zaman ilk tercihtir.
        fresh = original(
            symbol,
            df15m,
            df1h,
            df4h,
            current_price,
        )

        if fresh is not None:
            return fresh

        # IOTA tipi durum: klasik pullback kalıbı yok ama Pump gölge katmanı
        # güçlü devamı görmüşse, güncel 1H/4H yapı ve fiyat sapması yeniden
        # doğrulanarak ikinci Premium işlem yolu üretilebilir.
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

        # Uzun EMA200 geçmişi tamamlanmamış coinleri çöpe atma. Veri yaşına göre
        # 1H/15M adaptif yol veya çok yeni coinde 1M momentum yolu kullanılır.
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
    # Olgun Premium geçmiş performansı yalnız 15M_ENTRY kaynaklıdır. 5M erken
    # trade ayrı kanıt oluşana kadar kapalı kalır. TREND_CONTINUATION yeni giriş
    # üretse de canlı yön için mevcut olgun Premium edge şartını korur.
    strategy.ENABLE_5M_EARLY_TRADE = False

    # Canlı loglarda çok sayıda güçlü kurulum %0.25 eşiğinde daha aday olmadan
    # kayboluyordu. Burada yalnız ADAY YAKALAMA penceresini %0.35'e açıyoruz.
    # Canlı gönderim filtresi gevşemiyor: aday hâlâ Premium pending/price teyidi,
    # maliyet, geçmiş edge, market guard ve portföy risk kapılarından geçmek zorunda.
    strategy.MAX_LATE_ENTRY_DISTANCE_PERCENT = 0.35

    gate = profit.PremiumGate(bot.TRADE_LEDGER_FILE)
    pending_gate = confirmation.PendingConfirmationGate(gate)

    # Her uygun USDT perpetual ticker her tur görülür. Ana TOP300 her tur derin,
    # dışarıda kalanlar sıcak-aday + rotation ile ek derin taranır.
    bot.get_scan_coins = _make_all_coin_scanner(bot.get_scan_coins)

    # Yeni coinlerde mevcut kısa veri main.py tarafından <120 diye atılmasın.
    # Olgun strategy.py yine kendi >=220 mum şartını korur.
    bot.fetch_df = allcoins.make_adaptive_fetcher(bot.fetch_df)

    bot.analyze_mtf_trade = _make_pending_analyzer(
        bot.analyze_mtf_trade,
        pending_gate,
    )

    bot.is_entry_still_valid = _make_profit_gate(
        bot.is_entry_still_valid,
        gate,
        pending_gate,
    )

    # Aynı coinde ters yön fırsatı eski sinyal yüzünden tamamen kaybolmasın.
    # Aynı yön çakışması ve toplam/yön portföy risk limitleri korunur.
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

    # DCA1 yalnız yeterli 4H/1H/15M/5M geçmiş teyidi alabildiğinde çalışır;
    # dolayısıyla çok yeni coinlerde otomatik olarak devre dışı kalır.
    recovery.run(bot)

    bot.main()

    changed = profit.enrich_premium(bot.TRADE_LEDGER_FILE)
    report = profit.report()
    print("Profit V4 ledger cost enrichment:", changed)
    print("Premium LONG edge:", report["premium"]["long"])
    print("Premium SHORT edge:", report["premium"]["short"])
    print("Premium teyit bekleyen aday (run sonu):", pending_gate.pending_count())


if __name__ == "__main__":
    run()
