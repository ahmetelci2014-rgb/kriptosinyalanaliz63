import market_first_target_display as display


def test_short_target_shows_explicit_expected_floor_and_range():
    item = {
        "direction": "SHORT",
        "current_price": 6.0640,
        "technical_target": 6.0224,
        "expected_move_percent": 0.69,
        "expected_move_low_percent": 0.46,
        "expected_move_high_percent": 1.03,
        "technical_target_source": "TP2/R",
        "target_confidence": "ORTA",
    }
    text = display.explicit_target_lines(item)
    assert "Tahmini düşebileceği ana seviye: 6.0224" in text
    assert "Beklenen düşüş: ~%0.69" in text
    assert "Beklenen düşüş bölgesi:" in text
    assert "6.0361" in text
    assert "6.0015" in text
    assert "Güçlü senaryoda düşebileceği seviye: 6.0015" in text


def test_long_target_shows_explicit_expected_ceiling_and_range():
    item = {
        "direction": "LONG",
        "current_price": 87.8080,
        "technical_target": 88.4340,
        "expected_move_percent": 0.71,
        "expected_move_low_percent": 0.09,
        "expected_move_high_percent": 0.71,
        "technical_target_source": "15M direnç",
        "target_confidence": "YÜKSEK",
    }
    text = display.explicit_target_lines(item)
    assert "Tahmini çıkabileceği ana seviye: 88.434" in text
    assert "Beklenen yükseliş: ~%0.71" in text
    assert "Beklenen yükseliş bölgesi:" in text
    assert "87.887" in text
    assert "88.431" in text
    assert "Güçlü senaryoda çıkabileceği seviye: 88.431" in text


def test_missing_target_stays_empty():
    assert display.explicit_target_lines({"direction": "LONG", "current_price": 10}) == ""
