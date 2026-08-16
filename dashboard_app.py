"""Kripto Kontrol Merkezi - sabit uygulama giriş noktası.

Aktif ürün görünümü V3.32 tabanında kalır. Runtimefix katmanı masaüstünde V3.32.1'i,
mobil ana panelde V3.32.3'ü ve mobil Piyasa/Coin'de V3.32.4 sunucu görünümünü korur.
FREE/PREMIUM/ADMIN sınırları ve canlı işlem çekirdeği değiştirilmez.
"""
from __future__ import annotations

from dashboard_runtimefix_app import VERSION as ACTIVE_VERSION
from dashboard_runtimefix_app import main as _active_main
from dashboard_runtimefix_app import make_v3321_handler as make_handler

VERSION = ACTIVE_VERSION
ACTIVE_MODULE = "dashboard_runtimefix_app"


def main() -> None:
    _active_main()


if __name__ == "__main__":
    main()
