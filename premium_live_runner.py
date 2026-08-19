"""Premium MTF canlı Telegram giriş noktası için fırsat yakalama koruması.

Premium strateji puanlarını, ADX/hacim/giriş/TP-SL eşiklerini değiştirmez.
Aynı coinde eski ters-yön açık sinyal bulunduğunda yeni yönün analiz edilmesini
sağlar. Gerçek Premium işlem mesajını erken/izleme mesajlarından ayırmak için
Telegram başlığını açıkça "İŞLEM GİRİŞİ" olarak etiketler. Gerçek emir açmaz.
"""
from __future__ import annotations

from typing import Any, Callable

import opportunity_capture as capture


def make_clear_signal_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(original, "_clear_premium_entry_wrapped", False):
        return original

    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        if (
            "MTF FUTURES SİNYALİ" in text
            and "✅ İŞLEM GİRİŞİ — PREMIUM" not in text
        ):
            text = (
                "✅ İŞLEM GİRİŞİ — PREMIUM\n"
                "Giriş + TP + SL hazır. Bu, erken izleme mesajı değildir.\n\n"
                + text
            )
        return original(text, *args, **kwargs)

    wrapped._clear_premium_entry_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def apply_opportunity_capture(bot: Any) -> None:
    # Yön henüz analizden önce bilinmediği için sembol-bazlı erken skip kaldırılır.
    # Aynı yön açık işlem yine portfolio_risk tarafından hard-block edilir.
    bot.has_open_same_symbol = lambda symbol: False
    bot.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        bot.evaluate_portfolio_risk
    )
    bot.send_telegram = make_clear_signal_sender(bot.send_telegram)


def run(bot: Any | None = None) -> None:
    if bot is None:
        import main as bot  # type: ignore[no-redef]

    apply_opportunity_capture(bot)
    print(
        "Premium fırsat yakalama: ters-yön açık sinyal yeni fırsatı ENGELLEMEZ | "
        "gerçek Telegram girişi ayrı etiketli"
    )
    bot.main()


if __name__ == "__main__":
    run()
