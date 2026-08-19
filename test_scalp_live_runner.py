import unittest

import scalp_live_runner as runner


class ReactionGuardTests(unittest.TestCase):
    def test_short_requires_red_1m_reversal(self):
        def original(*args, **kwargs):
            return ({"direction": "SHORT", "move1": 0.03}, {"ok": True})

        wrapped = runner.make_reaction_confirmation_wrapper(original)
        signal, debug = wrapped()
        self.assertIsNone(signal)
        self.assertEqual(debug, {"ok": True})

    def test_short_allows_confirmed_red_1m_reversal(self):
        def original(*args, **kwargs):
            return ({"direction": "SHORT", "move1": -0.08}, {"ok": True})

        wrapped = runner.make_reaction_confirmation_wrapper(original)
        signal, _ = wrapped()
        self.assertIsNotNone(signal)

    def test_long_requires_green_1m_reversal(self):
        def original(*args, **kwargs):
            return ({"direction": "LONG", "move1": -0.03}, {"ok": True})

        wrapped = runner.make_reaction_confirmation_wrapper(original)
        signal, _ = wrapped()
        self.assertIsNone(signal)

    def test_long_allows_confirmed_green_1m_reversal(self):
        def original(*args, **kwargs):
            return ({"direction": "LONG", "move1": 0.08}, {"ok": True})

        wrapped = runner.make_reaction_confirmation_wrapper(original)
        signal, _ = wrapped()
        self.assertIsNotNone(signal)


if __name__ == "__main__":
    unittest.main()
