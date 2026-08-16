from __future__ import annotations

from pathlib import Path

import dashboard_adminhub_app as app


def test_admin_hub_contains_expected_internal_tools():
    html = app.admin_analysis_hub()
    for href in (
        "/learning-center",
        "/system-quality",
        "/early-performance",
        "/performance-intelligence",
        "/admin/improvement-center",
    ):
        assert f'href="{href}"' in html
    assert "CANLI KURAL YAZMAZ" in html


def test_admin_center_enhancement_is_idempotent():
    body = '<style></style><body><div class="quick"><a href="/x">X</a></div></body>'
    first = app.enhance_admin_center(body)
    second = app.enhance_admin_center(first)
    assert first.count('id="v321AdminAnalysisHub"') == 1
    assert second.count('id="v321AdminAnalysisHub"') == 1


def test_admin_hub_is_inserted_before_existing_quick_links():
    body = '<style></style><body><div class="quick"><a href="/admin/users">Kullanıcılar</a></div></body>'
    out = app.enhance_admin_center(body)
    assert out.index('id="v321AdminAnalysisHub"') < out.index('<div class="quick">')


def test_source_preserves_live_core_boundaries():
    source = Path(app.__file__).read_text(encoding="utf-8")
    assert 'signal_engine": "unchanged"' in source
    assert 'telegram": "unchanged"' in source
    assert 'trade_management": "unchanged"' in source
    assert 'ledger_write": "unchanged"' in source
    assert 'automatic_filter": False' in source
    assert "strategy.py" not in source


def test_version_is_v321():
    assert "V3_21" in app.VERSION
    assert "ADMIN_ANALYSIS_HUB" in app.VERSION
