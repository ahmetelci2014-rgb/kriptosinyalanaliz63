"""Top-level Premium runner with adaptive quality protections."""
from __future__ import annotations


def run() -> None:
    import premium_early_breakout as early
    import premium_quality_layer as quality
    import premium_regime_profit_runner as regime_runner
    import premium_regime_transition as regime

    quality.begin()
    quality.install(early, regime)
    print(
        "Premium Quality Layer:",
        quality.VERSION,
        "| skor kalibrasyonu + yön sağlığı + giriş zamanlaması + Market Outlook AKTİF",
    )
    try:
        regime_runner.run()
    finally:
        try:
            print("Premium Quality Layer özet:", quality.finish())
        except Exception as exc:
            print("Premium Quality Layer state kaydetme hatası:", exc)


if __name__ == "__main__":
    run()
