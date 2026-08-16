"""Kripto Kontrol Merkezi - sabit uygulama giriş noktası.

Bu dosya ürünün aktif panel katmanını tek yerden seçer. Docker ve operasyonel
başlatma komutları bundan sonra sürüm numaralı modüllere doğrudan bağlanmaz.
Yeni bir güvenli panel katmanı onaylandığında yalnız ACTIVE_MODULE / import
satırı ilerletilir; canlı sinyal motoruna dokunulmaz.
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
