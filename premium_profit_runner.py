"""Premium Profit Mode V2 - cost-aware live gate. No exchange orders."""
from __future__ import annotations
from typing import Any, Callable

import live_entry_safety as safety
import opportunity_capture as capture
import profitability_engine as profit
import strategy
import main as bot


def _make_clear_signal_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text=str(message or "")
        if "MTF FUTURES SİNYALİ" in text and "✅ İŞLEM GİRİŞİ — PREMIUM" not in text:
            text=("✅ İŞLEM GİRİŞİ — PREMIUM V2\n"
                  "Maliyet-sonrası geçmiş avantaj + canlı giriş teyidi geçti.\n\n"+text)
        return original(text,*args,**kwargs)
    return wrapped


def _make_profit_gate(original: Callable[..., Any], gate: profit.PremiumGate) -> Callable[..., Any]:
    def wrapped(signal: dict, current_price: Any):
        ok, reason=original(signal,current_price)
        if not ok:
            return ok, reason
        result=gate.evaluate(signal,current_price)
        signal["profit_mode_v2"]={
            "version":profit.VERSION,
            "decision":result.get("reason"),
            "timing":result.get("timing"),
            "evidence":result.get("evidence"),
        }
        if not result.get("ok"):
            gate.reject(signal,current_price,result)
            print("PROFIT V2 RED:",signal.get("symbol"),signal.get("direction"),result.get("reason"))
            return False, "Profit V2: "+str(result.get("reason"))
        return True, "Profit V2 uygun"
    return wrapped


def run() -> None:
    strategy.ENABLE_5M_EARLY_TRADE=False
    bot.MAX_TRADE_SIGNALS_PER_RUN=1
    bot.MAX_OPEN_SIGNALS=2
    bot.RISK_MODE_STOP_COUNT=2

    gate=profit.PremiumGate(bot.TRADE_LEDGER_FILE)
    bot.is_entry_still_valid=_make_profit_gate(bot.is_entry_still_valid,gate)

    bot.has_open_same_symbol=lambda symbol: False
    bot.evaluate_portfolio_risk=capture.make_opposite_direction_evaluator(bot.evaluate_portfolio_risk)

    bot.send_telegram=safety.make_entry_safety_sender(bot.send_telegram)
    bot.send_telegram=_make_clear_signal_sender(bot.send_telegram)

    print("PROFIT MODE V2 / PREMIUM | 15M | maliyet-sonrası edge + %5-40 TP1 teyit koridoru | 5M kapalı")
    bot.main()

    changed=profit.enrich_premium(bot.TRADE_LEDGER_FILE)
    report=profit.report()
    print("Profit V2 ledger cost enrichment:",changed)
    print("Premium LONG edge:",report["premium"]["long"])
    print("Premium SHORT edge:",report["premium"]["short"])


if __name__=="__main__":
    run()
