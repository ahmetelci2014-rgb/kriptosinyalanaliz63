from pathlib import Path

import market_first_scheduler_health as health


def test_first_run_is_baseline():
    payload = health.build_payload({}, 1_000)
    assert payload["status"] == "BASELINE"
    assert payload["last_interval_seconds"] is None
    assert payload["observed_count"] == 0


def test_five_minute_cadence_is_healthy():
    payload = health.build_payload({"last_run_at": 1_000}, 1_300)
    assert payload["status"] == "HEALTHY"
    assert payload["last_interval_seconds"] == 300
    assert payload["last_interval_minutes"] == 5.0


def test_fifteen_minute_cadence_is_severely_delayed():
    payload = health.build_payload({"last_run_at": 1_000}, 1_900)
    assert payload["status"] == "SEVERELY_DELAYED"
    assert payload["last_interval_seconds"] == 900
    assert payload["delayed_rate"] == 1.0


def test_history_is_bounded_and_summary_is_computed():
    prior = {
        "last_run_at": 10_000,
        "intervals_seconds": [300] * 60,
    }
    payload = health.build_payload(prior, 10_900)
    assert len(payload["intervals_seconds"]) == health.MAX_INTERVALS
    assert payload["intervals_seconds"][-1] == 900
    assert payload["observed_median_seconds"] == 300.0
    assert payload["delayed_count"] == 1


def test_run_persists_file(tmp_path):
    path = tmp_path / "health.json"
    payload = health.run(
        str(path),
        now=2_000,
        env={"GITHUB_RUN_ID": "123", "GITHUB_RUN_NUMBER": "7", "GITHUB_EVENT_NAME": "workflow_dispatch"},
    )
    assert path.exists()
    assert payload["run"]["id"] == "123"
    assert payload["run"]["number"] == "7"


def test_live_workflow_uses_day_trading_internal_five_minute_cadence():
    text = Path(".github/workflows/main.yml").read_text(encoding="utf-8")
    # Market First scheduler-health remains available for historical diagnostics,
    # but it is intentionally outside the new Day Trading live execution path.
    assert "python market_first_scheduler_health.py" not in text
    assert "market_first_scheduler_health.json" not in text
    assert "python day_trading_core.py" in text
    assert "cycles=3" in text
    assert "target_interval=300" in text
    # cron-job.org remains the single external trigger source.
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "cron:" not in text
