from __future__ import annotations

import json

import source_performance_report as report


def _trade(source, result, r_value, closed_at, direction="LONG"):
    return {
        "status": "CLOSED",
        "source": source,
        "final_result": result,
        "r_result": r_value,
        "entry": 100.0,
        "sl": 99.0,
        "closed_at": closed_at,
        "direction": direction,
    }


def test_generate_keeps_live_sources_separate(tmp_path):
    now_value = 1_000_000
    ledger = {
        "trades": {
            "a": _trade("15M_ENTRY", "TP3", 1.0, now_value - 100),
            "b": _trade("BIG_MOVE_ENTRY", "SL", -1.0, now_value - 200),
            "c": _trade(
                "REGIME_TRANSITION_ENTRY",
                "TP1_SONRASI_BE",
                0.275,
                now_value - 300,
                direction="SHORT",
            ),
        }
    }
    ledger_path = tmp_path / "trade_ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result = report.generate(str(ledger_path), now_value=now_value)
    lifetime = result["windows"]["lifetime"]

    assert lifetime["combined_live"]["sample"] == 3
    assert lifetime["combined_live"]["by_direction"]["LONG"]["sample"] == 2
    assert lifetime["combined_live"]["by_direction"]["SHORT"]["sample"] == 1
    assert lifetime["sources"]["15M_ENTRY"]["sample"] == 1
    assert lifetime["sources"]["BIG_MOVE_ENTRY"]["sample"] == 1
    assert lifetime["sources"]["REGIME_TRANSITION_ENTRY"]["sample"] == 1
    assert lifetime["sources"]["BIG_MOVE_ENTRY"]["result_counts"]["SL"] == 1
    assert lifetime["sources"]["REGIME_TRANSITION_ENTRY"]["direction_counts"]["SHORT"] == 1
    assert lifetime["sources"]["REGIME_TRANSITION_ENTRY"]["by_direction"]["SHORT"]["sample"] == 1
    assert lifetime["sources"]["REGIME_TRANSITION_ENTRY"]["by_direction"]["LONG"]["sample"] == 0
    assert lifetime["sources"]["15M_ENTRY"]["evidence_status"] == "INSUFFICIENT_SAMPLE"


def test_attach_preserves_existing_profit_report(tmp_path):
    now_value = 2_000_000
    ledger_path = tmp_path / "trade_ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "trades": {
                    "a": _trade("EARLY_BREAKOUT_ENTRY", "TP3", 1.0, now_value - 10),
                }
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "profit_mode_report.json"
    report_path.write_text(json.dumps({"premium": {"all": {"sample": 99}}}), encoding="utf-8")

    breakdown = report.attach_to_profit_report(str(ledger_path), str(report_path))
    stored = json.loads(report_path.read_text(encoding="utf-8"))

    assert stored["premium"]["all"]["sample"] == 99
    assert stored["live_source_breakdown"]["version"] == report.VERSION
    assert breakdown["windows"]["lifetime"]["sources"]["EARLY_BREAKOUT_ENTRY"]["sample"] == 1
    assert breakdown["windows"]["lifetime"]["sources"]["EARLY_BREAKOUT_ENTRY"]["by_direction"]["LONG"]["sample"] == 1
