from pathlib import Path


def test_autonomous_reverse_shadow_is_manual_only_and_stays_shadow_only():
    text = Path(".github/workflows/autonomous-reverse-shadow.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "python autonomous_reverse_shadow.py" in text
    assert "TOKEN:" not in text
    assert "CHAT_ID:" not in text
    assert "OKX_API_KEY:" not in text


def test_historical_ml_seed_is_manual_only():
    text = Path(".github/workflows/market-first-historical-ml-seed.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "push:" not in text
    assert "python market_first_historical_replay_v3.py" in text
