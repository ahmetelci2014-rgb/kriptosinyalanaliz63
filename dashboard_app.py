"""Kripto Kontrol Merkezi - sabit uygulama giriş noktası.

Aktif ürün görünümü V3.32 olarak korunur. V3.32.1 yalnız klasik Premium/Admin
panelde kırılan istemci runtime'ı için bağımsız navigasyon ve veri yükleme onarımı
ekler; FREE/PREMIUM sınırları ve canlı işlem çekirdeği değiştirilmez.
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
