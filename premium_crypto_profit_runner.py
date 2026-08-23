"""Premium live runner with crypto-only and post-TP3 reversal guards."""
from __future__ import annotations


def run() -> None:
    # Patch before premium_profit_runner imports premium_all_coins so every live
    # universe build in this process sees crypto-only market metadata.
    import all_market_shadow as market_scan
    from crypto_universe_guard import install_crypto_only_guard

    install_crypto_only_guard(market_scan)

    import premium_profit_runner
    from premium_reversal_capture import install as install_reversal_capture

    # Keep the legacy same-direction cooldown, but allow a strict opposite-side
    # Premium route after TP3 when fresh reversal structure is confirmed.
    install_reversal_capture(premium_profit_runner)

    premium_profit_runner.run()


if __name__ == "__main__":
    run()
