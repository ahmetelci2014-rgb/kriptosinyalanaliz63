import dashboard_chartlevel_app as app


def test_dashboard_smart_links_are_premium_presentation_only():
    body = '<html><head></head><body><div class="opp-card" data-focus-symbol="BTCUSDT">BTC</div></body></html>'
    rendered = app.enhance_dashboard_smart_links(body, 'nonce-1')
    assert 'v314-smart-link-script' in rendered
    assert '/coin-center?symbol=' in rendered
    assert 'data-focus-symbol' in rendered
    assert 'nonce="nonce-1"' in rendered
    assert 'location.assign' in rendered


def test_coin_page_adds_read_only_level_overlay():
    body = app.coin_center_page_v314('nonce-2', 'BTCUSDT')
    assert 'id="levelOverlay"' in body
    assert 'id="levelToggle"' in body
    assert 'id="levelLegend"' in body
    assert 'v314-level-script' in body
    assert 'Giriş' in body
    assert 'TP1' in body and 'TP2' in body and 'TP3' in body and 'SL' in body
    assert '/api/coin-center/summary?symbol=' in body
    assert 'LATEST_OPEN_SCENARIO' not in body  # health metadata only, not customer page text
    assert 'nonce="nonce-2"' in body


def test_level_overlay_uses_existing_chart_metadata_and_does_not_generate_signals():
    script = app.LEVEL_SCRIPT
    assert 'base.__chart' in script
    assert "['entry','tp1','tp2','tp3','sl']" in script
    assert 'state.trade' in script
    assert 'localStorage' in script
    assert 'fetch(`/api/coin-center/summary' in script
    assert 'order' not in script.lower()


def test_v314_version_and_safety_contract():
    assert 'V3_14' in app.VERSION
    source = open('dashboard_chartlevel_app.py', encoding='utf-8').read()
    assert 'dashboard_coin_app as coin' in source
    assert 'signal_engine":"unchanged"' in source
    assert 'telegram":"unchanged"' in source
    assert 'new_api_schedule":False' in source
