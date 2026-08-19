"""Premium Profit Mode V1.

Runs only the main 15M Premium trade path, limits live concurrency, keeps
opposite-direction opportunity visibility, and labels real Telegram entries
clearly. Profit Mode disables 5M early trades at runtime. No exchange orders.
"""
from __future__ import annotations

from typing import Any, Callable

import live_entry_safety as safety
import opportunity_capture as capture
import strategy
import main as bot


def _make_clear_signal_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        if (
            "MTF FUTURES SİNYALİ" in text
            and "✅ İŞLEM GİRİŞİ — PREMIUM" not in text
        ):
            text = (
                "✅ İŞLEM GİRİŞİ — PREMIUM\n"
                "Giriş + TP + SL hazır. Erken izleme mesajı değildir.\n\n"
                + text
            )
        return original(text, *args, **kwargs)

    return wrapped


def run() -> None:
    # Profit Mode overrides the base engine: no new 5M early live trades.
    strategy.ENABLE_5M_EARLY_TRADE = False

    # Capital protection first: fewer simultaneous live ideas.
    bot.MAX_TRADE_SIGNALS_PER_RUN = 1
    bot.MAX_OPEN_SIGNALS = 2
    bot.RISK_MODE_STOP_COUNT = 2

    # Do not hide a new opposite-direction opportunity before direction is known.
    # Same-direction duplicate and portfolio exposure rules remain downstream.
    bot.has_open_same_symbol = lambda symbol: False
    bot.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        bot.evaluate_portfolio_risk
    )

    bot.send_telegram = safety.make_entry_safety_sender(bot.send_telegram)
    bot.send_telegram = _make_clear_signal_sender(bot.send_telegram)

    print(
        "PROFIT MODE V1 / PREMIUM | 15M ana giriş | "
        "5M erken trade KAPALI | max yeni 1 | max açık 2 | risk modu 2 stop"
    )
    bot.main()


if __name__ == "__main__":
    run()
