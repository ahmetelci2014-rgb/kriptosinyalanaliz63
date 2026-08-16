from __future__ import annotations

import copy
import inspect
import unittest
from pathlib import Path

import dashboard_account_flow_app as flow
import dashboard_accountflow_runtime_app as runtime
import dashboard_accounts_app as accounts
import dashboard_app as app
import dashboard_runtimefix_app as runtimefix


class MemoryAccountStore(accounts.GitHubAccountStore):
    def __init__(self):
        super().__init__("owner/private", "token", ref="panel-users")
        self.document = self._empty_document()
        self.sha = None
        self.actions: list[tuple[str, str]] = []

    def _load_unlocked(self):
        return copy.deepcopy(self.document), self.sha

    def _save_unlocked(self, document, sha, *, actor, action):
        self.document = copy.deepcopy(document)
        self.sha = "next-sha"
        self.actions.append((actor, action))


class DashboardAccountFlowTests(unittest.TestCase):
    def _store(self) -> MemoryAccountStore:
        store = MemoryAccountStore()
        store.create_user(
            "uye01",
            "member-secret-123",
            role=accounts.ROLE_MEMBER,
            expiry_days="",
            actor="admin",
        )
        return store

    def test_managed_password_change_requires_current_password_and_replaces_hash(self):
        store = self._store()
        self.assertTrue(flow.managed_account(store, "UYE01"))
        flow.change_managed_password(
            store,
            "uye01",
            "member-secret-123",
            "new-member-secret-456",
            "new-member-secret-456",
        )
        self.assertIsNone(store.authenticate("uye01", "member-secret-123"))
        self.assertIsNotNone(store.authenticate("uye01", "new-member-secret-456"))
        self.assertEqual(store.actions[-1][1], "self-password-change uye01")

    def test_wrong_current_password_does_not_change_password(self):
        store = self._store()
        with self.assertRaisesRegex(ValueError, "Mevcut şifre doğru değil"):
            flow.change_managed_password(
                store,
                "uye01",
                "wrong-current-password",
                "new-member-secret-456",
                "new-member-secret-456",
            )
        self.assertIsNotNone(store.authenticate("uye01", "member-secret-123"))
        self.assertIsNone(store.authenticate("uye01", "new-member-secret-456"))

    def test_password_change_rejects_mismatch_same_and_short_password(self):
        store = self._store()
        with self.assertRaisesRegex(ValueError, "eşleşmiyor"):
            flow.change_managed_password(
                store, "uye01", "member-secret-123", "new-member-secret-456", "other-member-secret-456"
            )
        with self.assertRaisesRegex(ValueError, "aynı olmamalıdır"):
            flow.change_managed_password(
                store, "uye01", "member-secret-123", "member-secret-123", "member-secret-123"
            )
        with self.assertRaises(ValueError):
            flow.change_managed_password(
                store, "uye01", "member-secret-123", "short", "short"
            )

    def test_security_page_is_server_rendered_and_explains_unmanaged_account(self):
        session = {"username": "uye01", "csrf": "csrf-value"}
        managed = flow.security_page(session, managed=True)
        self.assertIn('action="/account/password"', managed)
        self.assertIn('name="current_password"', managed)
        self.assertIn('name="new_password_confirm"', managed)
        self.assertNotIn("<script", managed.lower())
        unmanaged = flow.security_page(session, managed=False)
        self.assertNotIn('action="/account/password"', unmanaged)
        self.assertIn("sunucu ortam ayarlarından yönetilir", unmanaged)

    def test_account_security_link_is_injected_once_for_mobile_or_desktop(self):
        mobile = '<html><head><style></style></head><body><div class="wrap"><div class="card">Hesap</div><form class="logout" method="post"></form></div></body></html>'
        enhanced = flow.enhance_account_security_link(mobile)
        self.assertIn('id="v3327AccountSecurity"', enhanced)
        self.assertIn('href="/account/security"', enhanced)
        self.assertLess(enhanced.index('id="v3327AccountSecurity"'), enhanced.index('<form class="logout"'))
        self.assertEqual(flow.enhance_account_security_link(enhanced).count('id="v3327AccountSecurity"'), 1)

    def test_payment_feedback_uses_only_fixed_safe_codes(self):
        base = '<html><head><style></style></head><body><div>Premium</div></body></html>'
        sent = flow.enhance_payment_feedback(base, "sent")
        self.assertIn('id="v3327PaymentFeedback"', sent)
        self.assertIn("Ödeme bildirimin kaydedildi", sent)
        invalid = flow.enhance_payment_feedback(base, "invalid")
        self.assertIn("Bilgileri kontrol edip tekrar deneyin", invalid)
        unknown = flow.enhance_payment_feedback(base, '<img src=x onerror=alert(1)>')
        self.assertNotIn("v3327PaymentFeedback", unknown)
        self.assertNotIn("onerror", unknown)

    def test_runtime_contract_preserves_v3326_and_adds_only_account_payment_flow(self):
        source = inspect.getsource(runtime)
        helper = inspect.getsource(flow)
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_accountflow_runtime_app")
        self.assertEqual(app.VERSION, runtime.VERSION)
        self.assertIn("V3_32_7_ACCOUNT_FLOW", runtime.VERSION)
        self.assertIn("runtimefix.make_v3321_handler", source)
        self.assertIn("/account/password", source)
        self.assertIn("/payment/notify", source)
        self.assertIn('"password_recovery": "not_enabled_without_verified_identity"', source)
        self.assertIn("V3_32_6_SURFACE_PARITY", runtimefix.VERSION)
        for forbidden in ("trade_ledger.json", "open_signals.json", "strategy.py", "config.py"):
            self.assertNotIn(forbidden, helper)
            self.assertNotIn(forbidden, source)

    def test_docker_and_journey_audit_include_account_flow_modules(self):
        docker = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
        ignore = Path(".dockerignore").read_text(encoding="utf-8")
        for name in ("dashboard_account_flow_app.py", "dashboard_accountflow_runtime_app.py"):
            self.assertIn(name, docker)
            self.assertIn("!" + name, ignore)
        audit = Path("docs/panel-journey-audit-v3327.md")
        self.assertTrue(audit.exists())
        text = audit.read_text(encoding="utf-8")
        for term in ("GİRİŞSİZ", "KAYIT", "FREE", "PREMIUM", "YENİLEME", "ADMIN", "Şifremi unuttum"):
            self.assertIn(term, text)


if __name__ == "__main__":
    unittest.main()
