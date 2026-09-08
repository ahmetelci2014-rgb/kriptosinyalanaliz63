from pathlib import Path

import market_first_promotion_reason_patch as patch


def test_direction_conflict_becomes_explicit_reason():
    plan = {"status": "ENTRY", "direction": "LONG"}
    decision = {"direction": "SHORT"}
    assert patch.classify_reason(plan, decision, "OK") == "ENTRY_PLAN_DIRECTION_CONFLICT"


def test_same_direction_preserves_fallback():
    plan = {"status": "ENTRY", "direction": "LONG"}
    decision = {"direction": "LONG"}
    assert patch.classify_reason(plan, decision, "OK") == "OK"


def test_non_entry_plan_preserves_fallback():
    plan = {"status": "PREP", "direction": "LONG"}
    decision = {"direction": "SHORT"}
    assert patch.classify_reason(plan, decision, "NO_ACCELERATION") == "NO_ACCELERATION"


def test_missing_decision_preserves_fallback():
    plan = {"status": "ENTRY", "direction": "LONG"}
    assert patch.classify_reason(plan, None, "OK") == "OK"


def test_live_install_order_places_reason_patch_before_tracking():
    text = Path("market_first_live_simple.py").read_text(encoding="utf-8")
    accelerator = text.index("entry_accelerator.install()")
    reason_patch = text.index("promotion_reason_patch.install()")
    tracking = text.index("complete_tracking.install_complete_tracking()")
    assert accelerator < reason_patch < tracking
