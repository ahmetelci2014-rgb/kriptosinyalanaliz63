"""Premium MTF canlı Telegram giriş noktası için fırsat yakalama koruması.

Premium strateji puanlarını, ADX/hacim/giriş/TP-SL eşiklerini değiştirmez.
Yalnız aynı coinde eski ters-yön açık sinyal bulunduğunda yeni yönün analiz
edilmesini ve bağımsız kalite şartlarını geçerse kullanıcıya söylenmesini sağlar.
Gerçek emir açmaz.
"""
from __future__ import annotations

from typing import Any

import opportunity_capture as capture


def apply_opportunity_capture(bot: Any) -> None:
    # Yön henüz analizden önce bilinmediği için sembol-bazlı erken skip kaldırılır.
    # Aynı yön açık işlem yine portfolio_risk tarafından hard-block edilir.
    bot.has_open_same_symbol = lambda symbol: False
    bot.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        bot.evaluate_portfolio_risk
    )


def run(bot: Any | None = None) -> None:
    if bot is None:
        import main as bot  # type: ignore[no-redef]

    apply_opportunity_capture(bot)
    print("Premium fırsat yakalama: ters-yön açık sinyal yeni fırsatı ENGELLEMEZ")
    bot.main()


if __name__ == "__main__":
    run()
