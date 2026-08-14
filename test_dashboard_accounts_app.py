import copy
import unittest

from dashboard_accounts_app import (
    GitHubAccountStore,
    ManagedSessionStore,
    ROLE_ADMIN,
    ROLE_MEMBER,
    _normalize_expiry_days,
    _normalize_password,
    _normalize_username,
)


class MemoryAccountStore(GitHubAccountStore):
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


class AccountStoreTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(_normalize_username("uye-01"), "uye-01")
        self.assertEqual(_normalize_expiry_days("30"), 30)
        self.assertIsNone(_normalize_expiry_days(""))
        self.assertEqual(_normalize_password("1234567890"), "1234567890")
        with self.assertRaises(ValueError):
            _normalize_username("../bad")
        with self.assertRaises(ValueError):
            _normalize_password("short")
        with self.assertRaises(ValueError):
            _normalize_expiry_days("99999")

    def test_create_and_authenticate_member(self):
        store = MemoryAccountStore()
        store.create_user(
            "demo-member",
            "member-secret-123",
            role=ROLE_MEMBER,
            expiry_days="30",
            actor="ahmet",
            reserved_usernames={"ahmet"},
        )
        rows = store.list_users()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username"], "demo-member")
        self.assertEqual(rows[0]["role"], ROLE_MEMBER)
        self.assertNotIn("password_hash", rows[0])
        self.assertEqual(
            store.authenticate("DEMO-MEMBER", "member-secret-123"),
            {"username": "demo-member", "role": ROLE_MEMBER},
        )
        self.assertIsNone(store.authenticate("demo-member", "wrong-password"))

    def test_reserved_and_duplicate_usernames_are_rejected(self):
        store = MemoryAccountStore()
        with self.assertRaises(ValueError):
            store.create_user(
                "ahmet",
                "admin-secret-123",
                role=ROLE_MEMBER,
                expiry_days="",
                actor="ahmet",
                reserved_usernames={"ahmet"},
            )
        store.create_user(
            "uye01",
            "member-secret-123",
            role=ROLE_MEMBER,
            expiry_days="",
            actor="ahmet",
        )
        with self.assertRaises(ValueError):
            store.create_user(
                "UYE01",
                "another-secret-123",
                role=ROLE_MEMBER,
                expiry_days="",
                actor="ahmet",
            )

    def test_disable_and_reenable_controls_login(self):
        store = MemoryAccountStore()
        store.create_user(
            "uye01",
            "member-secret-123",
            role=ROLE_MEMBER,
            expiry_days="",
            actor="ahmet",
        )
        store.set_active("uye01", False, actor="ahmet")
        self.assertIsNone(store.authenticate("uye01", "member-secret-123"))
        store.set_active("uye01", True, actor="ahmet")
        self.assertIsNotNone(store.authenticate("uye01", "member-secret-123"))

    def test_password_reset_invalidates_old_password(self):
        store = MemoryAccountStore()
        store.create_user(
            "uye01",
            "member-secret-123",
            role=ROLE_MEMBER,
            expiry_days="",
            actor="ahmet",
        )
        store.reset_password("uye01", "new-member-secret-456", actor="ahmet")
        self.assertIsNone(store.authenticate("uye01", "member-secret-123"))
        self.assertEqual(
            store.authenticate("uye01", "new-member-secret-456")["role"],
            ROLE_MEMBER,
        )

    def test_role_and_expiry_management(self):
        store = MemoryAccountStore()
        store.create_user(
            "operator01",
            "operator-secret-123",
            role=ROLE_MEMBER,
            expiry_days="",
            actor="ahmet",
        )
        store.set_role("operator01", ROLE_ADMIN, actor="ahmet")
        self.assertEqual(
            store.authenticate("operator01", "operator-secret-123")["role"],
            ROLE_ADMIN,
        )
        store.set_expiry("operator01", "1", actor="ahmet")
        row = store.list_users()[0]
        self.assertIsNotNone(row["expires_at"])
        self.assertFalse(row["expired"])

    def test_expired_account_cannot_authenticate(self):
        store = MemoryAccountStore()
        store.create_user(
            "uye01",
            "member-secret-123",
            role=ROLE_MEMBER,
            expiry_days="",
            actor="ahmet",
        )
        store.document["users"][0]["expires_at"] = 1
        self.assertIsNone(store.authenticate("uye01", "member-secret-123"))
        self.assertTrue(store.list_users()[0]["expired"])

    def test_managed_sessions_can_revoke_one_user_only(self):
        sessions = ManagedSessionStore(3600)
        token_a, _ = sessions.create("uye01", ROLE_MEMBER)
        token_b, _ = sessions.create("uye02", ROLE_MEMBER)
        self.assertIsNotNone(sessions.get(token_a))
        self.assertIsNotNone(sessions.get(token_b))
        sessions.delete_username("UYE01")
        self.assertIsNone(sessions.get(token_a))
        self.assertIsNotNone(sessions.get(token_b))


if __name__ == "__main__":
    unittest.main()
