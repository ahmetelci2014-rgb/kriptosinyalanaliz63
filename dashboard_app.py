"""Kripto Kontrol Merkezi - sabit uygulama giriş noktası.

Aktif panel runtime'ı burada seçilir. 2026-08-16 mobil/rol regresyonları nedeniyle
ürün görünümü son doğrulanmış V3.32 Market/Coin UX katmanına geri alınmıştır.
V3.33+ dosyaları repoda korunur ancak aktif runtime değildir. Canlı sinyal motoru,
Telegram, TP/SL/BE, state/ledger ve üyelik backend'i değiştirilmez.
"""
from __future__ import annotations

from dashboard_marketcoinux_app import VERSION as ACTIVE_VERSION
from dashboard_marketcoinux_app import main as _active_main
from dashboard_marketcoinux_app import make_v332_handler as make_handler

VERSION = ACTIVE_VERSION
ACTIVE_MODULE = "dashboard_marketcoinux_app"


def main() -> None:
    _active_main()


if __name__ == "__main__":
    main()
