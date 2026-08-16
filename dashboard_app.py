"""Kripto Kontrol Merkezi - sabit uygulama giriş noktası.

Aktif ürün görünümü V3.32 tabanında kalır. Masaüstü V3.32.1 ve mobil V3.32.3 ana
panel korunur; V3.32.4 yalnız mobil Piyasa/Coin rotalarını JS'siz sunucu görünümüne taşır.
FREE/PREMIUM/ADMIN sınırları ve canlı işlem çekirdeği değiştirilmez.
"""
from __future__ import annotations

from dashboard_mobile_market_app import VERSION as ACTIVE_VERSION
from dashboard_mobile_market_app import main as _active_main
from dashboard_mobile_market_app import make_v3324_handler as make_handler

VERSION = ACTIVE_VERSION
ACTIVE_MODULE = "dashboard_mobile_market_app"


def main() -> None:
    _active_main()


if __name__ == "__main__":
    main()
