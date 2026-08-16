"""Kripto Kontrol Merkezi - sabit uygulama giriş noktası.

Aktif ürün görünümü V3.32 tabanında kalır. Runtimefix masaüstünde V3.32.1'i, mobil
ana panelde V3.32.3'ü, Piyasa/Coin'de V3.32.4'ü ve Hesap/Premium'da V3.32.5'i
korur. V3.32.6 yalnız denetimde doğrulanan masaüstü/mobil/plan parite açıklarını
kapatır. FREE/PREMIUM/ADMIN sınırları ve canlı işlem çekirdeği değiştirilmez.
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
