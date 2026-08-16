"""Kripto Kontrol Merkezi - sabit uygulama giriş noktası.

Aktif ürün görünümü V3.32 tabanında kalır. V3.32.6 masaüstü/mobil/plan
paritesini, V3.32.7 hesap güvenliği/ödeme geri bildirimini korur. V3.32.8 yalnız
Premium/Admin yönetilen hesaplarda İzleme Listesini cihazlar arasında senkronlar;
kurucu/env hesapta cihaz-local fallback sürer. FREE/PREMIUM/ADMIN sınırları ve
canlı işlem çekirdeği değişmez.
"""
from __future__ import annotations

from dashboard_watchsync_runtime_app import VERSION as ACTIVE_VERSION
from dashboard_watchsync_runtime_app import main as _active_main
from dashboard_watchsync_runtime_app import make_v3321_handler as make_handler

VERSION = ACTIVE_VERSION
ACTIVE_MODULE = "dashboard_watchsync_runtime_app"


def main() -> None:
    _active_main()


if __name__ == "__main__":
    main()
