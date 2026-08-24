import early_breakout_direction_diagnostics as diag


def _trade(direction, result, code, *, closed_at=100_000, source="EARLY_BREAKOUT_ENTRY", status="COMPLETED"):
    return {
        "source": source,
        "direction": direction,
        "closed_at": closed_at,
        "final_result": result,
        "result_diagnostics": {
            "status": status,
            "final_result": result,
            "diagnosis": {
                "code": code,
                "likely_cause": "TEST_CAUSE",
            },
        },
    }


def test_direction_summary_separates_long_and_short():
    ledger = {
        "trades": {
            "l1": _trade("LONG", "SL", "SL_SONRASI_TERS_YON_DEVAMI"),
            "l2": _trade("LONG", "BE", "BE_SONRASI_TP3"),
            "s1": _trade("SHORT", "SL", "SL_SONRASI_TOPARLANMA"),
            "other": _trade("LONG", "SL", "SL_SONRASI_TERS_YON_DEVAMI", source="15M_ENTRY"),
        }
    }
    state = diag.build_state(ledger, now_ts=100_100)
    assert state["lifetime"]["LONG"]["tracked_diagnostics"] == 2
    assert state["lifetime"]["SHORT"]["tracked_diagnostics"] == 1
    assert state["lifetime"]["LONG"]["sl_direction_or_setup"] == 1
    assert state["lifetime"]["LONG"]["be_maybe_early"] == 1
    assert state["lifetime"]["SHORT"]["sl_recovery_timing"] == 1
    assert state["live_rule_action"] == "NONE_SHADOW_ONLY"


def test_long_direction_cluster_creates_watch_only():
    trades = {}
    for idx in range(4):
        trades[f"l{idx}"] = _trade("LONG", "SL", "SL_SONRASI_TERS_YON_DEVAMI", closed_at=190_000 + idx)
    ledger = {"trades": trades}
    state = diag.build_state(ledger, now_ts=200_000)
    assert "LONG_DIRECTION_SETUP_WATCH" in state["watch_flags"]
    assert state["live_rule_action"] == "NONE_SHADOW_ONLY"


def test_timing_recovery_cluster_is_not_called_wrong_direction():
    trades = {}
    for idx in range(4):
        trades[f"s{idx}"] = _trade("SHORT", "SL", "SL_SONRASI_GUCLU_TOPARLANMA", closed_at=290_000 + idx)
    state = diag.build_state({"trades": trades}, now_ts=300_000)
    assert "SHORT_ENTRY_STOP_TIMING_WATCH" in state["watch_flags"]
    assert "SHORT_DIRECTION_SETUP_WATCH" not in state["watch_flags"]
