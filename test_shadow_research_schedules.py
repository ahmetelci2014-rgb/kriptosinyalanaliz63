from pathlib import Path


def test_autonomous_reverse_shadow_runs_twice_hourly_and_stays_shadow_only():
    text = Path(".github/workflows/autonomous-reverse-shadow.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert 'cron: "11,41 * * * *"' in text
    assert "python autonomous_reverse_shadow.py" in text
    assert "TOKEN:" not in text
    assert "CHAT_ID:" not in text
    assert "OKX_API_KEY:" not in text


def test_historical_ml_seed_runs_daily_and_keeps_existing_triggers():
    text = Path(".github/workflows/market-first-historical-ml-seed.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert 'cron: "23 2 * * *"' in text
    assert "push:" in text
    assert "python market_first_historical_replay_v3.py" in text
