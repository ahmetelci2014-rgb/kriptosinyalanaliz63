import market_first_entry_condition_diagnosis as diagnosis


def episode(
    symbol="TESTUSDT",
    *,
    zone=True,
    score=86,
    s5="LONG",
    s15="LONG",
    s1h="LONG",
    volume5=0.9,
    risk=0.7,
    room=2.0,
    extension=0.5,
    tp1_at=1600,
    tp2=False,
    tp3=False,
    best_r=2.0,
):
    return {
        "episode_id": f"{symbol}:LONG:1000",
        "symbol": symbol,
        "direction": "LONG",
        "first_at": 1000,
        "resolved": True,
        "entry_signal_sent": False,
        "entry_condition_met": False,
        "zone_touched": zone,
        "tp1_at": tp1_at,
        "tp2_at": 1700 if tp2 else None,
        "tp3_at": 1800 if tp3 else None,
        "best_favorable_r": best_r,
        "best_favorable_percent": 2.0,
        "worst_adverse_percent": 0.2,
        "outcome": "TP3_REACHED" if tp3 else "TP2_REACHED" if tp2 else "TP1_REACHED",
        "latest_plan": {
            "score": score,
            "structure_5m": s5,
            "structure_15m": s15,
            "structure_1h": s1h,
            "volume_ratio_5m": volume5,
            "volume_ratio_15m": 1.0,
            "risk_percent": risk,
            "room_r": room,
            "extension_atr_5m": extension,
            "market_regime": "CHOP",
        },
    }


def test_zone_never_touched_is_primary_obstacle():
    report = diagnosis.build_report({"episodes": {"a": episode(zone=False)}}, generated_at=2000)
    assert report["summary"]["before_entry_condition_missed"] == 1
    assert report["primary_obstacles"]["ZONE_NEVER_TOUCHED"] == 1


def test_neutral_5m_is_visible_and_can_match_shadow_profile():
    row = episode(s5="NEUTRAL", score=88, volume5=0.6, tp3=True, best_r=3.2)
    report = diagnosis.build_report({"episodes": {"a": row}}, generated_at=2000)
    assert report["primary_obstacles"]["STRUCTURE_5M_NEUTRAL_LATEST"] == 1
    assert report["summary"]["shadow_pre_entry_profile_count"] == 1
    assert report["top_shadow_pre_entry_profiles"][0]["tp3_reached"] is True


def test_low_volume_flag_and_quick_move_are_counted():
    row = episode(volume5=0.2, tp1_at=2200)
    report = diagnosis.build_report({"episodes": {"a": row}}, generated_at=3000)
    assert report["primary_obstacles"]["VOLUME_5M_BELOW_0_50_LATEST"] == 1
    assert report["diagnostic_flags"]["VOLUME_5M_BELOW_0_50_LATEST"] == 1
    assert report["summary"]["tp1_within_30m"] == 1


def test_score_is_prioritized_before_neutral_structure():
    row = episode(score=70, s5="NEUTRAL", volume5=0.2)
    report = diagnosis.build_report({"episodes": {"a": row}}, generated_at=2000)
    assert report["primary_obstacles"]["SCORE_BELOW_76_LATEST"] == 1


def test_likely_timing_window_missed_when_latest_snapshot_passes_references():
    row = episode(score=90, s5="LONG", volume5=0.8, risk=0.8, room=3.0, extension=0.4, tp3=True)
    report = diagnosis.build_report({"episodes": {"a": row}}, generated_at=2000)
    assert report["primary_obstacles"]["LIKELY_TIMING_WINDOW_MISSED"] == 1
    assert report["summary"]["shadow_pre_entry_profile_count"] == 1


def test_legacy_and_sent_rows_are_excluded():
    legacy = episode(symbol="OLDUSDT")
    legacy["exclude_from_clean_prep_stats"] = True
    sent = episode(symbol="SENTUSDT")
    sent["entry_signal_sent"] = True
    condition = episode(symbol="CONDUSDT")
    condition["entry_condition_met"] = True
    report = diagnosis.build_report(
        {"episodes": {"legacy": legacy, "sent": sent, "condition": condition}}, generated_at=2000
    )
    assert report["summary"]["before_entry_condition_missed"] == 0
