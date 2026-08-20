import os
import tempfile
import unittest

import premium_confirmation as confirmation


class FakeGate:
    def __init__(self):
        self.profiles = {
            "LONG": {"live_allowed": True},
            "SHORT": {"live_allowed": True},
        }
        self.rejections = []

    def evaluate(self, signal, price):
        entry = float(signal["entry"])
        tp1 = float(signal["tp1"])
        current = float(price)
        direction = str(signal["direction"]).upper()

        if direction == "LONG":
            progress = (current - entry) / (tp1 - entry) * 100
        else:
            progress = (entry - current) / (entry - tp1) * 100

        distance = abs(current - entry) / entry * 100

        if progress < 5:
            reason = "CONFIRMATION_NOT_STARTED"
            ok = False
        elif progress > 40:
            reason = "MOVE_TOO_ADVANCED"
            ok = False
        elif distance < 0.08:
            reason = "PRICE_HAS_NOT_CONFIRMED_ENOUGH"
            ok = False
        elif distance > 0.25:
            reason = "ENTRY_TOO_FAR"
            ok = False
        else:
            reason = "PROFIT_MODE_V2_ALLOWED"
            ok = True

        return {
            "ok": ok,
            "reason": reason,
            "timing": {
                "tp1_progress_percent": progress,
                "entry_distance_percent": distance,
            },
            "evidence": self.profiles[direction],
        }

    def reject(self, signal, price, result):
        self.rejections.append(result["reason"])


class FakeStrategy:
    @staticmethod
    def get_4h_trend(_frame):
        return "LONG", "4H ana trend yukarı", {"adx_4h": 25}

    @staticmethod
    def get_1h_confirm(_frame):
        return "LONG", "1H alım onayı", {"adx_1h": 24}

    @staticmethod
    def trend_supports_direction(direction, trend, confirm, strict=True):
        return (
            str(direction).upper() == "LONG"
            and trend == "LONG"
            and confirm == "LONG"
        )


class FakeShortStrategy:
    @staticmethod
    def get_4h_trend(_frame):
        return "SHORT", "4H ana trend aşağı", {"adx_4h": 22}

    @staticmethod
    def get_1h_confirm(_frame):
        return "SHORT", "1H satış onayı", {"adx_1h": 23}

    @staticmethod
    def trend_supports_direction(direction, trend, confirm, strict=True):
        return (
            str(direction).upper() == "SHORT"
            and trend == "SHORT"
            and confirm == "SHORT"
        )


def base_validator(signal, price):
    entry = float(signal["entry"])
    tp1 = float(signal["tp1"])
    current = float(price)
    direction = str(signal["direction"]).upper()

    distance = abs(current - entry) / entry * 100
    if direction == "LONG":
        progress = (current - entry) / (tp1 - entry) * 100
    else:
        progress = (entry - current) / (entry - tp1) * 100

    if distance > 0.25:
        return False, "girişten uzak"
    if progress > 45:
        return False, "TP1 fazla ilerledi"
    return True, "uygun"


def long_signal(entry=100, signal_class="TRADE"):
    return {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "source": "15M_ENTRY",
        "signal_class": signal_class,
        "entry": entry,
        "sl": entry - 1,
        "tp1": entry + 1,
        "tp2": entry + 2,
        "tp3": entry + 3,
        "score": 96,
    }


class PendingConfirmationTests(unittest.TestCase):
    def test_first_candidate_is_anchored_and_later_confirms(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [1000]
            gate = FakeGate()
            pending = confirmation.PendingConfirmationGate(
                gate,
                os.path.join(folder, "state.json"),
                now_fn=lambda: now[0],
            )

            first = long_signal()
            ok, reason, _ = pending.evaluate(first, 100, base_validator)
            self.assertFalse(ok)
            self.assertIn("bekleniyor", reason)
            self.assertEqual(pending.pending_count(), 1)

            now[0] += 300
            refreshed = long_signal(entry=100.10)
            refreshed["score"] = 98

            ok, _, result = pending.evaluate(
                refreshed,
                100.12,
                base_validator,
            )

            self.assertTrue(ok)
            self.assertTrue(result["ok"])
            self.assertEqual(refreshed["entry"], 100)
            self.assertEqual(refreshed["score"], 98)
            self.assertEqual(
                refreshed["premium_confirmation"]["status"],
                "CONFIRMED",
            )
            self.assertEqual(pending.pending_count(), 0)

    def test_market_blocked_candidate_stays_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [1000]
            pending = confirmation.PendingConfirmationGate(
                FakeGate(),
                os.path.join(folder, "state.json"),
                now_fn=lambda: now[0],
            )

            first = long_signal(signal_class="RADAR")
            ok, reason, _ = pending.evaluate(first, 100.12, base_validator)

            self.assertFalse(ok)
            self.assertIn("market yön teyidi", reason)
            self.assertEqual(pending.pending_count(), 1)

            state = confirmation._load(pending.state_file)
            anchor = next(iter(state["pending"].values()))["signal"]
            self.assertEqual(anchor["signal_class"], "TRADE")

    def test_fallback_rechecks_without_fresh_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [1000]
            pending = confirmation.PendingConfirmationGate(
                FakeGate(),
                os.path.join(folder, "state.json"),
                now_fn=lambda: now[0],
            )

            first = long_signal()
            self.assertFalse(
                pending.evaluate(first, 100, base_validator)[0]
            )

            candles = [
                {"time": 1000, "high": 100.2, "low": 99.5},
            ]
            fallback = pending.fallback_signal(
                "BTCUSDT",
                candles,
                object(),
                object(),
                FakeStrategy,
            )

            self.assertIsNotNone(fallback)
            self.assertTrue(fallback["premium_pending_fallback"])
            self.assertEqual(fallback["entry"], 100)
            self.assertEqual(fallback["signal_class"], "TRADE")

            now[0] += 300
            ok, _, result = pending.evaluate(
                fallback,
                100.12,
                base_validator,
            )
            self.assertTrue(ok)
            self.assertTrue(result["ok"])

    def test_fallback_is_removed_if_tp1_was_seen(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [1000]
            pending = confirmation.PendingConfirmationGate(
                FakeGate(),
                os.path.join(folder, "state.json"),
                now_fn=lambda: now[0],
            )

            first = long_signal()
            self.assertFalse(
                pending.evaluate(first, 100, base_validator)[0]
            )

            candles = [
                {"time": 1000, "high": 101.01, "low": 99.7},
            ]
            fallback = pending.fallback_signal(
                "BTCUSDT",
                candles,
                object(),
                object(),
                FakeStrategy,
            )

            self.assertIsNone(fallback)
            self.assertEqual(pending.pending_count(), 0)

    def test_short_market_block_anchor_rechecks_as_trade(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [1000]
            pending = confirmation.PendingConfirmationGate(
                FakeGate(),
                os.path.join(folder, "state.json"),
                now_fn=lambda: now[0],
            )

            first = {
                "symbol": "APRUSDT",
                "direction": "SHORT",
                "source": "15M_ENTRY",
                "signal_class": "RADAR",
                "entry": 0.1835,
                "sl": 0.1854,
                "tp1": 0.1824,
                "tp2": 0.1814,
                "tp3": 0.1803,
                "score": 96,
            }

            self.assertFalse(
                pending.evaluate(first, 0.1835, base_validator)[0]
            )

            fallback = pending.fallback_signal(
                "APRUSDT",
                [{"time": 1000, "high": 0.1840, "low": 0.1833}],
                object(),
                object(),
                FakeShortStrategy,
            )

            self.assertIsNotNone(fallback)
            self.assertEqual(fallback["signal_class"], "TRADE")
            self.assertEqual(fallback["direction"], "SHORT")

    def test_expired_candidate_is_cleaned_from_state(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [1000]
            path = os.path.join(folder, "state.json")
            pending = confirmation.PendingConfirmationGate(
                FakeGate(),
                path,
                max_age_seconds=60,
                now_fn=lambda: now[0],
            )

            self.assertFalse(
                pending.evaluate(long_signal(), 100, base_validator)[0]
            )
            self.assertEqual(pending.pending_count(), 1)

            now[0] = 1100
            self.assertEqual(pending.pending_count(), 0)
            self.assertEqual(
                len(confirmation._load(path).get("pending", {})),
                0,
            )


if __name__ == "__main__":
    unittest.main()
