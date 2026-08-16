import inspect
import unittest

import dashboard_accountux_app as accountux
import dashboard_billing_app as billing
import dashboard_commercial_app as commercial


class FakeStore:
    def __init__(self, payments=None):
        self._payments = list(payments or [])

    def list_payments(self):
        return list(self._payments)


class AccountUxTests(unittest.TestCase):
    def session(self):
        return {"username": "member", "role": "MEMBER", "csrf": "csrf-test"}

    def free_info(self):
        return {"plan": commercial.PLAN_FREE, "expires_at": None, "active": True}

    def premium_info(self):
        return {"plan": commercial.PLAN_PREMIUM, "expires_at": 2_000_000_000, "active": True}

    def test_current_real_account_markers_are_supported(self):
        body = billing.account_page_v31(self.session(), self.free_info(), FakeStore())
        self.assertIn("Hesabım", body)
        self.assertIn("Premium bitiş", body)
        self.assertIn('class="grid"', body)
        enhanced = accountux.enhance_member_account_ui(body, "nonce-test", plan=commercial.PLAN_FREE)
        self.assertIn('id="v330-account-script"', enhanced)
        self.assertIn("Premium üyeliği incele", enhanced)
        self.assertIn("v330-hidden-primary", enhanced)

    def test_current_real_premium_markers_are_supported(self):
        body = billing.premium_page_v31(
            self.session(),
            self.free_info(),
            FakeStore(),
            {
                "days": 30,
                "package_name": "Premium 30 Gün",
                "price_label": "Fiyat için yöneticiyle iletişime geçin",
                "package_code": "PREMIUM_30D",
                "instructions": "Ödeme bilgisini yöneticiden alın.",
            },
            False,
        )
        self.assertIn('class="instructions"', body)
        self.assertIn("Ödeme geçmişim", body)
        enhanced = accountux.enhance_member_account_ui(body, "nonce-test", plan=commercial.PLAN_FREE)
        self.assertIn("Ödeme açıklamasını göster", enhanced)
        self.assertIn("Ödeme geçmişini göster", enhanced)
        self.assertIn("v330-secondary-note", enhanced)

    def test_account_layer_is_idempotent(self):
        body = billing.account_page_v31(self.session(), self.premium_info(), FakeStore())
        once = accountux.enhance_member_account_ui(body, "nonce-test", plan=commercial.PLAN_PREMIUM)
        twice = accountux.enhance_member_account_ui(once, "nonce-test", plan=commercial.PLAN_PREMIUM)
        self.assertEqual(once, twice)
        self.assertEqual(once.count('id="v330-account-script"'), 1)

    def test_admin_membership_route_is_not_targeted_by_presentation_transform(self):
        source = inspect.getsource(accountux)
        self.assertIn('path in {"/account", "/premium"}', source)
        self.assertNotIn('path in {"/account", "/premium", "/admin/memberships"}', source)
        self.assertIn('"admin_membership_screen": "preserved"', source)

    def test_backend_and_live_core_are_unchanged(self):
        source = inspect.getsource(accountux)
        self.assertNotIn("def do_POST", source)
        self.assertIn("flowux.make_v329_handler", source)
        self.assertIn('"membership_backend": "unchanged"', source)
        self.assertIn('"payment_backend": "unchanged"', source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == "__main__":
    unittest.main()
