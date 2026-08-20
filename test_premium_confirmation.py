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

            first = {
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "source": "15M_ENTRY",
                "entry": 100,
                "sl": 99,
                "tp1": 101,
                "tp2": 102,
                "tp3": 103,
                "score": 96,
            }

            ok, reason, _ = pending.evaluate(first, 100, base_validator)
            self.assertFalse(ok)
            self.assertIn("bekleniyor", reason)
            self.assertEqual(pending.pending_count(), 1)

            now[0] += 300
            refreshed = {
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "source": "15M_ENTRY",
                "entry": 100.10,
                "sl": 99.10,
                "tp1": 101.10,
                "tp2": 102.10,
                "tp3": 103.10,
                "score": 98,
            }

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

    def test_expired_candidate_restarts_with_current_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [1000]
            gate = FakeGate()
            pending = confirmation.PendingConfirmationGate(
                gate,
                os.path.join(folder, "state.json"),
                max_age_seconds=60,
                now_fn=lambda: now[0],
            )

            first = {
                "symbol": "ETHUSDT",
                "direction": "LONG",
                "source": "15M_ENTRY",
                "entry": 100,
                "sl": 99,
                "tp1": 101,
                "tp2": 102,
                "tp3": 103,
            }
            self.assertFalse(
                pending.evaluate(first, 100, base_validator)[0]
            )

            now[0] = 1100
            refreshed = dict(first)
            refreshed["entry"] = 101
            refreshed["tp1"] = 102
            refreshed["tp2"] = 103
            refreshed["tp3"] = 104

            self.assertFalse(
                pending.evaluate(refreshed, 101, base_validator)[0]
            )
            self.assertEqual(pending.pending_count(), 1)


if __name__ == "__main__":
    unittest.main()
