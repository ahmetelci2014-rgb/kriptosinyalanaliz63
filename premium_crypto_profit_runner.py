"""Premium live runner with a crypto-only OKX perpetual universe guard."""
from __future__ import annotations


def run() -> None:
    # Patch before premium_profit_runner imports premium_all_coins so every live
    # universe build in this process sees crypto-only market metadata.
    import all_market_shadow as market_scan
    from crypto_universe_guard import install_crypto_only_guard

    install_crypto_only_guard(market_scan)

    import premium_profit_runner

    premium_profit_runner.run()


if __name__ == "__main__":
    run()
