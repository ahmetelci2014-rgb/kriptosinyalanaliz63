import market_outlook_runner as runner


def test_manual_delivery_key_prefers_github_run_id(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    assert runner.manual_delivery_key({"ts": 999}) == "MANUAL_12345"


def test_scheduled_run_does_not_force_extra_report(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    assert runner.send_manual_report_if_needed({"sent": False, "snapshot": {"ts": 1}}, "t", "c") is False


def test_manual_run_sends_when_daily_report_already_sent(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_RUN_ID", "777")

    captured = {}

    monkeypatch.setattr(runner, "build_message_v2", lambda snapshot, state: "V2 RAPOR")

    def fake_send(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(runner, "send_telegram_once", fake_send)

    result = {
        "sent": False,
        "snapshot": {"ts": 123, "outlook": {}},
        "accuracy": {"6h": {"sample": 1}},
    }

    assert runner.send_manual_report_if_needed(result, "TOKEN", "CHAT") is True
    assert captured["message"] == "V2 RAPOR"
    assert captured["bot_key"] == "MARKET_OUTLOOK"
    assert captured["delivery_key"] == "MANUAL_777"


def test_manual_run_does_not_double_send_if_daily_send_happened(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    assert runner.send_manual_report_if_needed({"sent": True, "snapshot": {"ts": 1}}, "t", "c") is False
