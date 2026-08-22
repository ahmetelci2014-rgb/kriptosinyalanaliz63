from compact_shadow_states import compact_state


def _v1_record(i, open_record=False):
    return {
        "id": f"V1_{i}",
        "symbol": "AAAUSDT",
        "direction": "LONG",
        "stage": "PREP",
        "max_stage": "TRIGGER" if i % 2 else "ARMED",
        "score": 80 + i,
        "created_at": 1000 + i,
        "outcome": None if open_record else "SUCCESS_FIRST",
        "features": {"heavy": "x" * 100},
        "conditions": {"a": True},
    }


def _v2_record(i):
    return {
        "id": f"V2_{i}",
        "symbol": "BBBUSDT",
        "direction": "SHORT",
        "initial_stage": "ARMED",
        "best_stage": "TRIGGER",
        "initial_score": 78,
        "best_score": 91,
        "started_at": 2000 + i,
        "first_resolution": "R2_FIRST" if i % 2 else "STOP_FIRST",
        "max_favorable_r": 2.5,
        "max_adverse_r": 0.4,
        "features": {"heavy": list(range(100))},
        "conditions": {"many": True},
    }


def _v3_record(i):
    return {
        "id": f"V3_{i}",
        "symbol": "CCCUSDT",
        "direction": "LONG",
        "base_stage": "TRIGGER",
        "base_score": 92,
        "started_at": 3000 + i,
        "status": "R2_FIRST",
        "first_confirmed_at": 3010 + i,
        "initial_flow": {"book_imbalance": 0.4, "huge": "y" * 100},
        "initial_conditions": {"strong_flow": True},
    }


def _snapshot(i):
    return {
        "symbol": "CCCUSDT",
        "direction": "LONG",
        "at": 4000 + i,
        "base_stage": "TRIGGER",
        "base_score": 93,
        "orderflow_score": 70,
        "orderflow_confirmed": True,
        "pressure_delta": 0.2,
        "flow": {
            "book_imbalance": 0.3,
            "top_imbalance": 0.2,
            "spread_bps": 2.0,
            "trade_imbalance": 0.4,
            "recent_trade_imbalance": 0.5,
            "trades_per_second": 8.0,
            "big_payload": "z" * 100,
        },
        "conditions": {"strong_flow": True, "spread_ok": True},
    }


def test_v1_keeps_open_full_and_archives_old_closed():
    state = {
        "records": [_v1_record(i) for i in range(5)] + [_v1_record(99, open_record=True)],
        "open": {"AAAUSDT|LONG": "V1_99"},
    }
    compact_state(state, "v1", recent_closed=2, archive_limit=10, now_ts=9999)

    ids = {row["id"] for row in state["records"]}
    assert "V1_99" in ids
    assert {"V1_3", "V1_4"}.issubset(ids)
    assert len(state["archive_records"]) == 3
    assert all("features" not in row and "conditions" not in row for row in state["archive_records"])
    assert state["open"]["AAAUSDT|LONG"] == "V1_99"


def test_v2_compaction_is_idempotent_and_rolls_over_archive():
    state = {"records": [_v2_record(i) for i in range(7)], "open": {}}
    compact_state(state, "v2", recent_closed=2, archive_limit=3, now_ts=9999)

    assert len(state["records"]) == 2
    assert len(state["archive_records"]) == 3
    assert state["archive_rollup"]["count"] == 2
    first_archive_ids = [row["id"] for row in state["archive_records"]]

    compact_state(state, "v2", recent_closed=2, archive_limit=3, now_ts=10000)
    assert [row["id"] for row in state["archive_records"]] == first_archive_ids
    assert state["archive_rollup"]["count"] == 2


def test_v3_keeps_recent_full_snapshots_and_compacts_old_flow():
    state = {
        "records": [_v3_record(i) for i in range(6)],
        "open": {"CCCUSDT_LONG": {"id": "OPEN", "symbol": "CCCUSDT"}},
        "snapshots": [_snapshot(i) for i in range(8)],
    }
    compact_state(
        state,
        "v3",
        recent_closed=2,
        archive_limit=10,
        recent_snapshots=3,
        snapshot_archive_limit=10,
        now_ts=9999,
    )

    assert len(state["records"]) == 2
    assert len(state["archive_records"]) == 4
    assert len(state["snapshots"]) == 3
    assert len(state["archive_snapshots"]) == 5
    assert all("flow" not in row and "conditions" not in row for row in state["archive_snapshots"])
    assert state["archive_snapshots"][0]["book_imbalance"] == 0.3
    assert state["compaction"]["open_records"] == 1


def test_v3_snapshot_rollup_preserves_overflow_counts():
    state = {"records": [], "open": {}, "snapshots": [_snapshot(i) for i in range(10)]}
    compact_state(
        state,
        "v3",
        recent_closed=0,
        archive_limit=2,
        recent_snapshots=2,
        snapshot_archive_limit=3,
        now_ts=9999,
    )
    assert len(state["archive_snapshots"]) == 3
    assert state["snapshot_rollup"]["count"] == 5
    assert state["snapshot_rollup"]["confirmed"] == 5
