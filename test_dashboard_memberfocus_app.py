import unittest

import dashboard_memberfocus_app as memberfocus


class MemberFocusTests(unittest.TestCase):
    def test_member_home_injects_focus_and_mobile_results_once(self):
        body = '''<html><head><style>.x{}</style></head><body>
        <section class="page active" id="page-home"><div class="page-head"></div><div class="summary" id="homeMetrics"></div></section>
        <nav class="mobile-nav"><button data-view="home">Ana</button><a href="/account"><span>○</span>Hesap</a></nav>
        </body></html>'''
        out = memberfocus.enhance_member_home(body, "nonce123")
        self.assertIn('id="v323MemberFocus"', out)
        self.assertIn('data-v323-view="signals"', out)
        self.assertIn('data-v323-view="trades"', out)
        self.assertIn('data-v323-view="results"', out)
        self.assertIn('id="v323CoinInput"', out)
        self.assertIn('Coin Merkezi', out)
        self.assertIn('v323-mobile-results', out)
        self.assertIn('nonce="nonce123"', out)
        self.assertEqual(memberfocus.enhance_member_home(out, "nonce123"), out)

    def test_coin_shortcut_is_read_only_navigation(self):
        self.assertIn('/coin-center?symbol=', memberfocus.SCRIPT)
        self.assertNotIn('/api/order', memberfocus.SCRIPT)
        self.assertNotIn('method="post"', memberfocus.member_focus_block().lower())

    def test_version_and_safety_contract(self):
        self.assertIn("V3_23_MEMBER_FOCUS", memberfocus.VERSION)
        source = open("dashboard_memberfocus_app.py", "r", encoding="utf-8").read()
        self.assertIn('"signal_engine":"unchanged"', source)
        self.assertIn('"telegram":"unchanged"', source)
        self.assertIn('"ledger_write":"unchanged"', source)
        self.assertNotIn("strategy.py", source)


if __name__ == "__main__":
    unittest.main()
