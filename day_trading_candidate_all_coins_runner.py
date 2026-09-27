"""Run the observation-only candidate ledger across all active OKX USDT swaps."""
import day_trading_core as core
import day_trading_candidate_tracker as tracker


def run() -> None:
    core.MAX_SCAN_COINS = 100_000
    core.MIN_24H_QUOTE_VOLUME = 0.0
    tracker.run()


if __name__ == "__main__":
    run()
