from __future__ import annotations

from pathlib import Path

import dashboard_learningfix_app as app


def test_learning_link_uses_native_root_anchor_before_v319_decoration():
    body = '<nav><a class="nav-item" href="/market-center"><span>M</span><b>Piyasa</b></a></nav>'
    out = app.ensure_learning_navigation(body, "/")
    assert 'href="/learning-center"' in out
    assert out.index('href="/learning-center"') < out.index('href="/market-center"')


def test_learning_link_is_idempotent():
    body = '<a class="nav-item" href="/learning-center"><span>◎</span><b>Öğrenme</b></a><a class="nav-item" href="/market-center">M</a>'
    out = app.ensure_learning_navigation(body, "/")
    assert out.count('href="/learning-center"') == 1


def test_non_root_page_is_not_modified():
    body = '<a class="nav-item" href="/market-center">M</a>'
    assert app.ensure_learning_navigation(body, "/coin-center") == body


def test_source_boundaries_stay_read_only():
    source = Path(app.__file__).read_text(encoding="utf-8")
    assert 'signal_engine": "unchanged"' in source
    assert 'telegram": "unchanged"' in source
    assert 'trade_management": "unchanged"' in source
    assert 'ledger_write": "unchanged"' in source
    assert 'automatic_filter": False' in source
    assert "strategy.py" not in source


def test_version_marks_navigation_fix():
    assert "V3_20_R1" in app.VERSION
    assert "LEARNING_NAV_FIX" in app.VERSION
