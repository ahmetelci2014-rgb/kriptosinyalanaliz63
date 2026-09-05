import market_first_direction_engine as engine
from market_first_strategy import MarketContext


def _context(*, breadth=0.50, preferred=None, regime="BULL"):
    return MarketContext(
        regime=regime,
        preferred_direction=preferred,
        score=30.0 if preferred != "SHORT" else -30.0,
        strength=30.0,
        breadth_5m=breadth,
        breadth_24h=0.50,
        major_move_5m_percent=0.0,
        allow_countertrend=True,
        majors={},
    )


def _structure(direction):
    return {
        "direction": direction,
        "atr": 1.0,
        "ema20": 100.0,
        "ema50": 100.0,
        "swing_low_12": 98.0,
        "swing_high_12": 102.0,
        "range_low_72": 95.0,
        "range_high_72": 105.0,
        "volume_ratio": 1.0,
        "extension_atr": 0.2,
    }


def _patch_structures(monkeypatch, directions):
    values = iter([_structure(item) for item in directions])
    monkeypatch.setattr(engine.strategy, "_structure", lambda *_args, **_kwargs: next(values))


def test_virtual_like_long_is_reclassified_as_short_preparation(monkeypatch):
    # The old candle structure still says LONG, but short-term breadth and live
    # aggressive flow are already strongly bearish, with a concentrated ask wall.
    _patch_structures(monkeypatch, ["LONG", "LONG", "LONG"])
    decision = {
        "direction": "LONG",
        "move_3m_percent": 0.0,
        "move_5m_percent": 0.0,
        "breakout_20m": False,
        "taker_available": True,
        "taker_imbalance": -0.425666,
        "cvd_available": True,
        "cvd_ratio": -0.425666,
        "cvd_impulse": 0.0,
        "book_available": True,
        "book_imbalance": 0.0,
        "book_opposing_wall_ratio": 6.101,
    }
    selected, diag = engine.choose_direction(
        decision=decision,
        df5m=object(),
        df15m=object(),
        df1h=object(),
        current_price=100.0,
        context=_context(breadth=0.29, preferred=None),
    )
    assert selected == "SHORT"
    assert diag["reversal"] is True
    assert diag["short"]["score"] >= engine.MIN_REVERSAL_SCORE
    assert diag["margin"] >= engine.MIN_REVERSAL_MARGIN
    assert diag["confirmations"] >= engine.MIN_REVERSAL_CONFIRMATIONS
    assert diag["confirmation_flags"]["taker"] is True
    assert diag["confirmation_flags"]["cvd"] is True
    assert diag["confirmation_flags"]["breadth"] is True


def test_mirror_short_is_reclassified_as_long_when_buy_flow_reverses(monkeypatch):
    _patch_structures(monkeypatch, ["SHORT", "SHORT", "SHORT"])
    decision = {
        "direction": "SHORT",
        "move_3m_percent": 0.0,
        "move_5m_percent": 0.0,
        "breakout_20m": False,
        "taker_available": True,
        "taker_imbalance": 0.43,
        "cvd_available": True,
        "cvd_ratio": 0.43,
        "cvd_impulse": 0.0,
        "book_available": True,
        "book_imbalance": 0.0,
        "book_opposing_wall_ratio": 6.2,
    }
    selected, diag = engine.choose_direction(
        decision=decision,
        df5m=object(),
        df15m=object(),
        df1h=object(),
        current_price=100.0,
        context=_context(breadth=0.71, preferred=None, regime="BEAR"),
    )
    assert selected == "LONG"
    assert diag["reversal"] is True
    assert diag["long"]["score"] >= engine.MIN_REVERSAL_SCORE


def test_breadth_conflict_alone_does_not_auto_flip(monkeypatch):
    _patch_structures(monkeypatch, ["LONG", "LONG", "LONG"])
    decision = {
        "direction": "LONG",
        "move_3m_percent": 0.0,
        "move_5m_percent": 0.0,
        "breakout_20m": False,
        "taker_available": False,
        "cvd_available": False,
        "book_available": False,
    }
    selected, diag = engine.choose_direction(
        decision=decision,
        df5m=object(),
        df15m=object(),
        df1h=object(),
        current_price=100.0,
        context=_context(breadth=0.29, preferred=None),
    )
    assert selected is None
    assert diag["reason"] in {"DIRECTION_MARGIN_WEAK", "REVERSAL_NOT_CONFIRMED"}


def test_single_order_book_wall_cannot_reverse_by_itself(monkeypatch):
    _patch_structures(monkeypatch, ["LONG", "LONG", "LONG"])
    decision = {
        "direction": "LONG",
        "move_3m_percent": 0.0,
        "move_5m_percent": 0.0,
        "breakout_20m": False,
        "taker_available": False,
        "cvd_available": False,
        "book_available": True,
        "book_imbalance": 0.0,
        "book_opposing_wall_ratio": 9.0,
    }
    selected, diag = engine.choose_direction(
        decision=decision,
        df5m=object(),
        df15m=object(),
        df1h=object(),
        current_price=100.0,
        context=_context(breadth=0.50, preferred=None),
    )
    assert selected != "SHORT"
    assert diag["confirmation_flags"]["opposing_wall"] in {True, False}


def test_aligned_long_evidence_keeps_long(monkeypatch):
    _patch_structures(monkeypatch, ["LONG", "LONG", "LONG"])
    decision = {
        "direction": "LONG",
        "move_3m_percent": 0.20,
        "move_5m_percent": 0.35,
        "breakout_20m": True,
        "taker_available": True,
        "taker_imbalance": 0.40,
        "cvd_available": True,
        "cvd_ratio": 0.38,
        "cvd_impulse": 0.35,
        "book_available": True,
        "book_imbalance": 0.18,
        "book_opposing_wall_ratio": 1.5,
    }
    selected, diag = engine.choose_direction(
        decision=decision,
        df5m=object(),
        df15m=object(),
        df1h=object(),
        current_price=100.0,
        context=_context(breadth=0.70, preferred="LONG"),
    )
    assert selected == "LONG"
    assert diag["reversal"] is False
    assert diag["long"]["score"] > diag["short"]["score"]
