"""Kripto Kontrol Merkezi - sabit uygulama giriş noktası.

Masaüstünde çalışan V3.32.1 klasik runtime korunur. V3.32.2 yalnız mobil istekte
JavaScript SPA yerine sunucu taraflı plan-aware görünüm kullanır; FREE/PREMIUM/ADMIN
sınırları ve canlı işlem çekirdeği değiştirilmez.
"""
from __future__ import annotations

from dashboard_mobile_server_app import VERSION as ACTIVE_VERSION
from dashboard_mobile_server_app import main as _active_main
from dashboard_mobile_server_app import make_v3322_handler as make_handler

VERSION = ACTIVE_VERSION
ACTIVE_MODULE = "dashboard_mobile_server_app"


def main() -> None:
    _active_main()


if __name__ == "__main__":
    main()
