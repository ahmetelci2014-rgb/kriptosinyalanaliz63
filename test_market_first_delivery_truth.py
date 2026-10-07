from market_first_delivery_truth import build_report


def test_promoted_unsent_is_not_counted_as_telegram_failure():
    report = build_report(
        {
            "funnel": {
                "entry_promoted": 100,
                "entry_signal_sent": 4,
            }
        },
        {
            "entry_plan_clean": {
                "entry_promoted": 100,
                "entry_signal_sent": 4,
            },
            "entry_plan": {
                "entry_send_failed": 3,
            },
        },
        {"run_counts": {"ACCEPTED": 8, "LOW_SCORE": 2}},
        {"run_counts": {"ACCEPTED": 5, "TAKER_CVD_OPPOSITE": 3}},
        {"run_counts": {"ACCEPTED": 1, "RECOVERY_SCORE": 4}},
        {"health": {"mode": "RECOVERY_STRICT", "reason": "POOR_ROLLING_STOP_RATE"}},
        generated_at=123,
    )

    truth = report["funnel_truth"]
    assert truth["internal_promoted"] == 100
    assert truth["real_signal_sent"] == 4
    assert truth["promoted_unsent"] == 96
    assert truth["real_telegram_send_failures"] == 3
    assert truth["internal_final_admission_gap"] == 93
    assert truth["promotion_is_send_attempt"] is False
    # Current V6 Balanced Core does not install the legacy survival gate.
    # A real send failure therefore remains the authoritative delivery status.
    assert report["survival"]["mode"] == "NOT_IN_LIVE_PATH"
    assert report["status"] == "TELEGRAM_DELIVERY_FAILURE_PRESENT"
    assert report["latest_gate_layers"]["capital_survival"]["blocks"] == {}
    assert report["top_latest_blocker"] == {
        "layer": "final_execution",
        "reason": "TAKER_CVD_OPPOSITE",
        "count": 3,
    }


def test_upstream_rejected_is_not_double_counted_as_new_blocker():
    report = build_report(
        {"funnel": {"entry_promoted": 2, "entry_signal_sent": 2}},
        {"entry_plan_clean": {}, "entry_plan": {}},
        {"run_counts": {"ACCEPTED": 2}},
        {"run_counts": {"UPSTREAM_REJECTED": 7, "ACCEPTED": 2}},
        {"run_counts": {"UPSTREAM_REJECTED": 9, "NORMAL_PASS": 2, "ACCEPTED": 2}},
        {"health": {"mode": "NORMAL", "reason": "OK"}},
        generated_at=456,
    )

    assert report["status"] == "OK"
    assert report["latest_gate_layers"]["final_execution"]["blocks"] == {}
    assert report["latest_gate_layers"]["capital_survival"]["blocks"] == {}
    assert report["top_latest_blocker"] is None
