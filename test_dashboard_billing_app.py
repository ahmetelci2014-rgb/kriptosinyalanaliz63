import os
import unittest
from unittest.mock import patch

import dashboard_billing_app as billing
import dashboard_commercial_app as commercial


class FakeStore:
    def __init__(self, payments=None, users=None):
        self._payments = list(payments or [])
        self._users = list(users or [])

    def list_payments(self):
        return list(self._payments)

    def list_commercial_users(self):
        return list(self._users)


class DashboardBillingV31Tests(unittest.TestCase):
    def test_version(self):
        self.assertIn("V3_1_BILLING", billing.VERSION)

    def test_settings_are_configurable_without_code_change(self):
        with patch.dict(os.environ, {
            "PANEL_PREMIUM_DAYS": "45",
            "PANEL_PREMIUM_PACKAGE_NAME": "Premium Plus",
            "PANEL_PREMIUM_PRICE_LABEL": "499 TL",
            "PANEL_PREMIUM_PACKAGE_CODE": "PREMIUM_45D",
        }, clear=False):
            settings = billing._settings()
        self.assertEqual(settings["days"], 45)
        self.assertEqual(settings["package_name"], "Premium Plus")
        self.assertEqual(settings["price_label"], "499 TL")
        self.assertEqual(settings["package_code"], "PREMIUM_45D")

    def test_payment_counts(self):
        store = FakeStore(payments=[
            {"status": "PENDING"},
            {"status": "APPROVED"},
            {"status": "APPROVED"},
            {"status": "REJECTED"},
        ])
        self.assertEqual(
            billing.payment_counts(store),
            {"total": 4, "pending": 1, "approved": 2, "rejected": 1},
        )

    def test_user_payments_only_returns_current_user(self):
        store = FakeStore(payments=[
            {"username": "ali", "status": "PENDING"},
            {"username": "veli", "status": "APPROVED"},
            {"username": "ALI", "status": "REJECTED"},
        ])
        rows = billing.user_payments(store, "Ali")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(str(row["username"]).casefold() == "ali" for row in rows))

    def test_premium_page_pending_prevents_duplicate_form(self):
        store = FakeStore(payments=[{
            "id": "PAY-1",
            "username": "uye",
            "method": "BANK_TRANSFER",
            "package": "PREMIUM_30D",
            "status": "PENDING",
            "created_at": 1_786_000_000,
        }])
        body = billing.premium_page_v31(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            {"plan": commercial.PLAN_FREE, "expires_at": None},
            store,
            {"days": 30, "package_name": "Premium 30 Gün", "price_label": "499 TL", "package_code": "PREMIUM_30D", "instructions": "FAST ile ödeme"},
            False,
        )
        self.assertIn("Ödeme bildirimin alındı", body)
        self.assertIn("Onay bekliyor", body)
        self.assertIn("499 TL", body)
        self.assertNotIn('action="/payment/notify"', body)

    def test_premium_page_free_user_has_payment_form_when_no_pending(self):
        body = billing.premium_page_v31(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            {"plan": commercial.PLAN_FREE, "expires_at": None},
            FakeStore(),
            {"days": 30, "package_name": "Premium 30 Gün", "price_label": "Fiyat sonra", "package_code": "PREMIUM_30D", "instructions": "Ödeme bilgisi"},
            False,
        )
        self.assertIn('action="/payment/notify"', body)
        self.assertIn('name="package" value="PREMIUM_30D"', body)
        self.assertIn("Ödeme yaptım · Onaya gönder", body)
        self.assertNotIn("Kripto ödeme bildirimi</option>", body)

    def test_account_page_shows_latest_payment_status(self):
        store = FakeStore(payments=[{
            "username": "uye",
            "package": "PREMIUM_30D",
            "status": "APPROVED",
            "created_at": 1_786_000_000,
            "decided_at": 1_786_000_100,
        }])
        body = billing.account_page_v31(
            {"username": "uye", "role": "MEMBER"},
            {"plan": commercial.PLAN_PREMIUM, "expires_at": 1_900_000_000},
            store,
        )
        self.assertIn("Son ödeme durumu", body)
        self.assertIn("Onaylandı", body)
        self.assertIn("Premium", body)

    def test_admin_page_has_counts_history_and_package_config(self):
        store = FakeStore(
            payments=[{
                "id": "PAY-1",
                "username": "uye",
                "method": "BANK_TRANSFER",
                "package": "PREMIUM_30D",
                "status": "PENDING",
                "created_at": 1_786_000_000,
                "note": "Ahmet",
            }],
            users=[{
                "username": "uye",
                "role": "MEMBER",
                "plan": "FREE",
                "expires_at": None,
            }],
        )
        body = billing.admin_billing_page(
            store,
            {"username": "admin", "role": "ADMIN", "csrf": "csrf-admin"},
            {"days": 30, "package_name": "Premium 30 Gün", "price_label": "499 TL", "package_code": "PREMIUM_30D", "instructions": "x"},
        )
        self.assertIn("Onay bekliyor", body)
        self.assertIn("Ödeme geçmişi", body)
        self.assertIn("499 TL", body)
        self.assertIn("Onayla +30 gün", body)
        self.assertIn("PAY-1", body)


if __name__ == "__main__":
    unittest.main()
