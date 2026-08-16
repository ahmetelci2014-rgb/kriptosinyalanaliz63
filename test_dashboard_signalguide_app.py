import json
from pathlib import Path

import dashboard_signalguide_app as app


def _write(path: Path, name: str, value):
    (path / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_enrichment_whitelists_member_safe_signal_context(tmp_path):
    raw = {
        "BTCUSDT_LONG_15M_ENTRY": {
            "trade_id": "trade-1",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "quality": "A",
            "quality_note": "Giriş zamanlama kontrolü geçti.",
            "trend_reason": "4H ana trend yukarı",
            "confirm_reason": "1H alış onayı",
            "entry_reason": "15M giriş kontrolü geçti",
            "entry_distance_at_send_percent": 0.12,
            "risk_percent": 0.85,
            "leverage": "2x",
            "market_guard_reason": "GİZLİ İÇ DETAY",
            "portfolio_risk": {"warnings": ["GİZLİ"]},
            "radar_reason": "GİZLİ RADAR DETAYI",
            "closed": False,
        }
    }
    _write(tmp_path, "open_signals.json", raw)
    _write(tmp_path, "scalp_radar_state.json", {})
    _write(tmp_path, "pump_radar_state.json", {})
    data = {"open_trades": [{"id": "trade-1", "system": "PREMIUM", "symbol": "BTCUSDT", "direction": "LONG"}]}
    result = app.enrich_open_trade_details(data, tmp_path)
    row = result["open_trades"][0]
    assert row["quality"] == "A"
    assert row["trend_reason"] == "4H ana trend yukarı"
    assert row["confirm_reason"] == "1H alış onayı"
    assert row["entry_reason"] == "15M giriş kontrolü geçti"
    assert row["entry_distance_at_send_percent"] == 0.12
    assert row["risk_percent"] == 0.85
    assert row["leverage"] == "2x"
    assert "market_guard_reason" not in row
    assert "portfolio_risk" not in row
    assert "radar_reason" not in row


def test_timing_labels_cover_near_started_far_and_adverse():
    base = {"entry": 100.0, "sl": 98.0, "direction": "LONG"}
    assert app._timing({**base, "last_price": 100.1})["label"] == "Giriş bölgesine yakın"
    assert app._timing({**base, "last_price": 100.6})["label"] == "Hareket başlamış"
    assert app._timing({**base, "last_price": 101.4})["label"] == "Girişten uzaklaşmış"
    assert app._timing({**base, "last_price": 99.0})["label"] == "Ters hareket var"


def test_tp_hit_overrides_timing_distance():
    row = {"entry": 100, "last_price": 110, "sl": 98, "direction": "LONG", "tp1_hit": True}
    result = app._timing(row)
    assert result["label"] == "TP1 görüldü"
    assert "yeni giriş çağrısı değildir" in result["detail"]


def test_sent_timing_and_sl_distance_are_explanatory_not_trade_commands():
    assert "çok yakındı" in app._sent_timing({"entry_distance_at_send_percent": 0.14})
    assert app._risk_distance({"stop_percent": 0.8})["label"] == "SL mesafesi dar"
    assert app._risk_distance({"stop_percent": 1.4})["label"] == "SL mesafesi orta"
    assert app._risk_distance({"stop_percent": 2.1})["label"] == "SL mesafesi geniş"


def test_guidance_uses_recorded_reasons_and_quality_note():
    data = {"open_trades": [{
        "id": "x", "symbol": "AAVEUSDT", "system_label": "Premium MTF", "direction": "SHORT",
        "entry": 100, "sl": 101, "last_price": 99.8, "score": 93, "quality": "A- TP1",
        "trend_reason": "4H ana trend aşağı", "confirm_reason": "1H satış onayı",
        "entry_reason": "15M giriş kontrolü geçti", "quality_note": "Dikkat: hacim düşük",
        "entry_distance_at_send_percent": 0.07,
    }]}
    rows = app.build_signal_guidance(data)
    assert len(rows) == 1
    assert rows[0]["quality"] == "A- TP1"
    assert rows[0]["reasons"] == ["4H ana trend aşağı", "1H satış onayı", "15M giriş kontrolü geçti"]
    assert rows[0]["note"] == "Dikkat: hacim düşük"


def test_member_page_injection_contains_plain_language_guide():
    body = '<html><head><style></style></head><body><div class="summary" id="homeMetrics"></div></body></html>'
    out = app.enhance_signal_guide(body, "nonce123")
    assert 'id="v324SignalGuide"' in out
    assert "Sinyal Rehberi" in out
    assert "NEDEN GELDİ?" in app.SCRIPT
    assert "Girişten uzaklaşmış" in app.SCRIPT
    assert "otomatik işlem" in out
    assert 'nonce="nonce123"' in out


def test_source_contract_preserves_live_core_boundaries():
    source = Path("dashboard_signalguide_app.py").read_text(encoding="utf-8")
    assert "do_POST" not in source
    assert '"signal_engine":"unchanged"' in source
    assert '"telegram":"unchanged"' in source
    assert '"ledger_write":"unchanged"' in source
    assert '"internal_strategy_details_exposed":False' in source
    assert "market_guard_reason" not in app.SAFE_DETAIL_KEYS
    assert "portfolio_risk" not in app.SAFE_DETAIL_KEYS


def test_docker_runs_v324_signal_guide():
    docker = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
    ignore = Path(".dockerignore").read_text(encoding="utf-8")
    assert "dashboard_signalguide_app.py" in docker
    assert 'CMD ["python", "dashboard_signalguide_app.py", "--host", "0.0.0.0"]' in docker
    assert "!dashboard_signalguide_app.py" in ignore
