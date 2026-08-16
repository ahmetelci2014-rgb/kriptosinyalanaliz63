"""Kripto Kontrol Merkezi - sabit uygulama giriş noktası.

Aktif ürün görünümü V3.32 tabanında kalır. V3.32.6 masaüstü/mobil/plan
paritesini korur; V3.32.7 yalnız doğrulanan hesap akışı açıklarını kapatır:
oturum içinden mevcut şifreyle güvenli şifre değiştirme ve ödeme bildirimi
geri bildirimi. FREE/PREMIUM/ADMIN sınırları ve canlı işlem çekirdeği değişmez.
"""
from __future__ import annotations

from dashboard_accountflow_runtime_app import VERSION as ACTIVE_VERSION
from dashboard_accountflow_runtime_app import main as _active_main
from dashboard_accountflow_runtime_app import make_v3321_handler as make_handler

VERSION = ACTIVE_VERSION
ACTIVE_MODULE = "dashboard_accountflow_runtime_app"


def main() -> None:
    _active_main()


if __name__ == "__main__":
    main()
