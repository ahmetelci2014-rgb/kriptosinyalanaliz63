import test_zz_5m_early_entry_replay_research as replay


def test_existing_5m_early_trade_broad_no_lookahead_replay():
    replay.SYMBOLS = (
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "SUIUSDT",
        "XRPUSDT",
        "DOGEUSDT",
        "LINKUSDT",
        "AVAXUSDT",
    )
    replay.EVAL_DAYS = 7
    replay.test_existing_5m_early_trade_no_lookahead_replay()
