from __future__ import annotations

import inspect
import time
import unittest
from pathlib import Path

import dashboard_app as app
import dashboard_commercial_app as commercial
import dashboard_mobile_account_app as mobileaccount
import dashboard_runtimefix_app as runtimefix


class FakeStore:
    def __init__(self, payments=None):
        self.payments = list(payments or [])

    def list_payments(self):
        return list(self.payments)


class MobileAccountTests(unittest.TestCase):
    def setUp(self):
        self.session = {"username": "uye", "csrf": "csrf-mobile"}
        self.settings = {
            "days": 30,
            "package_name": "Premium 30 Gün",
            "price_label": "₺999",
            "package_code": "PREMIUM_30D",
            "instructions": "FAST/Havale sonrası bildirim gönder.",
        }

    def test_free_account_is_simple_and_javascript_free(self):
        body = mobileaccount.render_account_page(
            self.session,
            {"plan": commercial.PLAN_FREE, "expires_at": None},
            plan=commercial.PLAN_FREE,
            plan_label="Ücretsiz",
            store=FakeStore(),
        )
        self.assertIn("Hesabım", body)
        self.assertIn("FREE plan aktiftir", body)
        self.assertIn("Üyelik merkezi", body)
        self.assertNotIn("Premium bitiş", body)
        self.assertNotIn("<script", body.lower())

    def test_free_premium_page_uses_existing_payment_notify_form(self):
        body = mobileaccount.render_premium_page(
            self.session,
            {"plan": commercial.PLAN_FREE, "expires_at": None},
            plan=commercial.PLAN_FREE,
            plan_label="Ücretsiz",
            store=FakeStore(),
            settings=self.settings,
            crypto_enabled=False,
        )
        self.assertIn('action="/payment/notify"', body)
        self.assertIn('name="csrf" value="csrf-mobile"', body)
        self.assertIn('name="package" value="PREMIUM_30D"', body)
        self.assertIn("Ödeme yaptım · Onaya gönder", body)
        self.assertIn("Ödeme açıklamasını göster", body)
        self.assertIn("Geçmiş işlemleri göster", body)
        self.assertNotIn("<script", body.lower())

    def test_pending_payment_hides_duplicate_form(self):
        payments = [{
            "username": "uye",
            "status": commercial.PAYMENT_PENDING,
            "package": "Premium 30 Gün",
            "method": "BANK_TRANSFER",
            "created_at": int(time.time()),
        }]
        body = mobileaccount.render_premium_page(
            self.session,
            {"plan": commercial.PLAN_FREE},
            plan=commercial.PLAN_FREE,
            plan_label="Ücretsiz",
            store=FakeStore(payments),
            settings=self.settings,
            crypto_enabled=False,
        )
        self.assertIn("yönetici onayı bekliyor", body)
        self.assertNotIn('action="/payment/notify"', body)
        self.assertIn("Onay bekliyor", body)

    def test_premium_last_seven_days_gets_renewal_form(self):
        expiry = int(time.time()) + 2 * 86400
        body = mobileaccount.render_premium_page(
            self.session,
            {"plan": commercial.PLAN_PREMIUM, "expires_at": expiry},
            plan=commercial.PLAN_PREMIUM,
            plan_label="Premium",
            store=FakeStore(),
            settings=self.settings,
            crypto_enabled=True,
        )
        self.assertIn("bitmesine 2 gün kaldı", body)
        self.assertIn("Yenileme ödemesi yaptım · Onaya gönder", body)
        self.assertIn('action="/payment/notify"', body)
        self.assertIn("Kripto ödeme bildirimi", body)

    def test_active_premium_does_not_show_payment_form_outside_renewal_window(self):
        expiry = int(time.time()) + 20 * 86400
        body = mobileaccount.render_premium_page(
            self.session,
            {"plan": commercial.PLAN_PREMIUM, "expires_at": expiry},
            plan=commercial.PLAN_PREMIUM,
            plan_label="Premium",
            store=FakeStore(),
            settings=self.settings,
            crypto_enabled=False,
        )
        self.assertIn("Premium üyeliğin aktif", body)
        self.assertNotIn('action="/payment/notify"', body)

    def test_runtime_routes_mobile_account_without_defining_post(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_runtimefix_app")
        self.assertEqual(app.VERSION, runtimefix.VERSION)
        self.assertIs(app.make_handler, runtimefix.make_v3321_handler)
        source = inspect.getsource(runtimefix)
        self.assertIn("_serve_mobile_account", source)
        self.assertIn("_serve_mobile_premium", source)
        self.assertIn('path in {"/mobile/account", "/account"}', source)
        self.assertIn('path in {"/mobile/premium", "/premium"}', source)
        self.assertIn('"mobile_account": "server_rendered_no_javascript"', source)
        self.assertIn('"membership_backend": "unchanged"', source)
        self.assertIn('"payment_backend": "unchanged"', source)
        self.assertNotIn("def do_POST", source)
        dockerfile = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        self.assertIn("dashboard_mobile_account_app.py", dockerfile)
        self.assertIn("!dashboard_mobile_account_app.py", dockerignore)


if __name__ == "__main__":
    unittest.main()
