from pathlib import Path
from unittest.mock import patch

import day_trading_all_coins_runner as runner
import day_trading_candidate_all_coins_runner as candidate_runner
import day_trading_core as core


def test_enable_full_universe_removes_top50_and_volume_prefilter():
    old_max = core.MAX_SCAN_COINS
    old_min = core.MIN_24H_QUOTE_VOLUME
    try:
        runner._enable_full_universe()
        assert core.MAX_SCAN_COINS >= 100_000
        assert core.MIN_24H_QUOTE_VOLUME == 0.0
    finally:
        core.MAX_SCAN_COINS = old_max
        core.MIN_24H_QUOTE_VOLUME = old_min


def test_rank_prefers_higher_quality_before_volume():
    high_score = {"trade": {"score": 95, "risk_percent": 0.8}, "quote_volume": 300_000}
    high_volume = {"trade": {"score": 90, "risk_percent": 0.8}, "quote_volume": 50_000_000}
    assert runner._rank_key(high_score) > runner._rank_key(high_volume)


def test_candidate_runner_expands_universe_before_tracking():
    old_max = core.MAX_SCAN_COINS
    old_min = core.MIN_24H_QUOTE_VOLUME
    try:
        with patch.object(candidate_runner.tracker, "run") as tracker_run:
            candidate_runner.run()
            tracker_run.assert_called_once_with()
            assert core.MAX_SCAN_COINS >= 100_000
            assert core.MIN_24H_QUOTE_VOLUME == 0.0
    finally:
        core.MAX_SCAN_COINS = old_max
        core.MIN_24H_QUOTE_VOLUME = old_min


def test_day_trading_remains_available_but_market_first_is_live():
    workflow = Path(".github/workflows/main.yml").read_text(encoding="utf-8")
    shadow = Path("day_trading_4h_shadow.py").read_text(encoding="utf-8")
    day_runner = Path("day_trading_all_coins_runner.py").read_text(encoding="utf-8")

    # Day Trading code remains in the repository for rollback/research, but the
    # live dispatcher was intentionally restored to Market First V6.
    assert "Market First V6 - Balanced Core Live" in workflow
    assert "python market_first_live_simple.py" in workflow
    assert "python day_trading_all_coins_runner.py" not in workflow

    # The inactive Day Trading research files keep their original timeframe
    # semantics so they remain usable if we ever compare or roll back again.
    assert 'core._fetch_df(exchange, ccxt_symbol, "2h")' in day_runner
    assert 'core._fetch_df(exchange, ccxt_symbol, "4h")' in shadow
    assert "does not block or promote live Day Trading signals" in shadow
