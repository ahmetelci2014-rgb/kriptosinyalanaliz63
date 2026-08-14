import copy
import time
import unittest

from dashboard_accounts_app import ROLE_ADMIN, ROLE_MEMBER
from dashboard_product_app import (
    ProductAccountStore,
    account_profile_page,
    account_summary,
    membership_remaining,
)


class MemoryProductStore(ProductAccountStore):
    def __init__(self):
        super().__init__("owner/private", "token", ref="panel-users")
        self.document = self._empty_document()
        self.sha = None
        self.actions = []

    def _load_unlocked(self):
        return copy.deepcopy(self.document), self.sha

    def _save_unlocked(self, document, sha, *, actor, action):
        self.document = copy.deepcopy(document)
        self.sha = "next-sha"
        self.actions.append((actor, action))


class ProductMembershipTests(unittest.TestCase):
    def test_delete_user_removes_login(self):
        store = MemoryProductStore()
        store.create_user(
            "testuye01",
            "member-secret-123",
            role=ROLE_MEMBER,
            expiry_days="30",
            actor="ahmet",
        )
        self.assertIsNotNone(store.authenticate("testuye01", "member-secret-123"))
        store.delete_user("testuye01", actor="ahmet")
        self.assertIsNone(store.authenticate("testuye01", "member-secret-123"))
        self.assertEqual(store.list_users(), [])
        self.assertEqual(store.actions[-1], ("ahmet", "delete testuye01"))

    def test_delete_unknown_user_fails(self):
        store = MemoryProductStore()
        with self.assertRaises(ValueError):
            store.delete_user("testuye01", actor="ahmet")

    def test_account_summary_counts_states(self):
        now = int(time.time())
        users = [
            {"role": ROLE_MEMBER, "active": True, "expired": False, "expires_at": now + 3 * 86400},
            {"role": ROLE_MEMBER, "active": False, "expired": False, "expires_at": None},
            {"role": ROLE_MEMBER, "active": True, "expired": True, "expires_at": now - 10},
            {"role": ROLE_ADMIN, "active": True, "expired": False, "expires_at": None},
        ]
        summary = account_summary(users)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["active"], 2)
        self.assertEqual(summary["passive"], 1)
        self.assertEqual(summary["expired"], 1)
        self.assertEqual(summary["expiring_7d"], 1)
        self.assertEqual(summary["admins"], 1)
        self.assertEqual(summary["members"], 3)

    def test_membership_remaining(self):
        self.assertEqual(membership_remaining(None), "Süresiz")
        self.assertEqual(membership_remaining(1), "Süresi doldu")
        future = int(time.time()) + 3600
        self.assertEqual(membership_remaining(future), "1 gün kaldı")

    def test_profile_page_dynamic_member_hides_secrets(self):
        store = MemoryProductStore()
        store.create_user(
            "uye01",
            "member-secret-123",
            role=ROLE_MEMBER,
            expiry_days="30",
            actor="ahmet",
        )
        body = account_profile_page(
            store,
            {"username": "uye01", "role": ROLE_MEMBER},
        )
        self.assertIn("uye01", body)
        self.assertIn("Üye", body)
        self.assertIn("Dinamik üyelik hesabı", body)
        self.assertNotIn("member-secret-123", body)
        self.assertNotIn("password_hash", body)
        self.assertNotIn("GITHUB_PANEL_USERS_TOKEN", body)

    def test_profile_page_bootstrap_admin(self):
        store = MemoryProductStore()
        body = account_profile_page(
            store,
            {"username": "ahmet", "role": ROLE_ADMIN},
        )
        self.assertIn("Yönetici", body)
        self.assertIn("Kurucu veya uyumluluk hesabı", body)
        self.assertIn("Süresiz", body)


if __name__ == "__main__":
    unittest.main()
