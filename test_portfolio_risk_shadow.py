import json
from pathlib import Path

from portfolio_risk import evaluate_portfolio_risk, shadow_ledger_file_for_bot


def _write(path, data):
    Path(path).write_text(json.dumps(data), encoding="utf-8")


def test_shadow_ledger_records_allow_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    sources = {
        "MAIN_MTF": {
            "filename": "open_signals.json",
            "containers": [None],
        }
    }
    _write("open_signals.json", {})

    result = evaluate_portfolio_risk(
        "BTC/USDT:USDT",
        "LONG",
        "MAIN_MTF",
        state_sources=sources,
        shadow_ledger_file="portfolio_risk_shadow.json",
    )
    assert result["hard_block"] is False

    ledger = json.loads(Path("portfolio_risk_shadow.json").read_text(encoding="utf-8"))
    assert ledger["summary"]["total_records"] == 1
    assert ledger["records"][0]["decision"] == "ALLOW"

    evaluate_portfolio_risk(
        "BTCUSDT",
        "LONG",
        "MAIN_MTF",
        state_sources=sources,
        shadow_ledger_file="portfolio_risk_shadow.json",
    )
    ledger = json.loads(Path("portfolio_risk_shadow.json").read_text(encoding="utf-8"))
    assert ledger["summary"]["total_records"] == 1


def test_default_shadow_ledger_is_isolated_per_bot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sources = {
        "MAIN_MTF": {
            "filename": "open_signals.json",
            "containers": [None],
        }
    }
    _write("open_signals.json", {})

    evaluate_portfolio_risk(
        "ETHUSDT",
        "LONG",
        "MAIN_MTF",
        state_sources=sources,
    )

    expected = Path(shadow_ledger_file_for_bot("MAIN_MTF"))
    assert expected.name == "portfolio_risk_shadow_main_mtf.json"
    assert expected.exists()
    assert not Path("portfolio_risk_shadow.json").exists()


def test_shadow_ledger_records_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    sources = {
        "SCALP": {
            "filename": "scalp.json",
            "containers": ["open_scalp_signals"],
        }
    }
    _write(
        "scalp.json",
        {
            "open_scalp_signals": {
                "btc": {
                    "symbol": "BTCUSDT",
                    "direction": "SHORT",
                    "entry": 100,
                    "tp1_hit": False,
                }
            }
        },
    )

    result = evaluate_portfolio_risk(
        "BTCUSDT",
        "LONG",
        "MAIN_MTF",
        state_sources=sources,
        shadow_ledger_file="portfolio_risk_shadow.json",
    )
    assert result["hard_block"] is True
    assert result["block_code"] == "SAME_COIN_OPPOSITE_DIRECTION"

    ledger = json.loads(Path("portfolio_risk_shadow.json").read_text(encoding="utf-8"))
    assert ledger["summary"]["blocked_records"] == 1
    assert ledger["records"][0]["would_block"] is True
