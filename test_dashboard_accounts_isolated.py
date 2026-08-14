import os
import unittest
from pathlib import Path
from unittest import mock

from dashboard_accounts_app import PanelConfig
from dashboard_accounts_isolated import isolated_account_store_from_env


class IsolatedAccountStoreTests(unittest.TestCase):
    def config(self):
        return PanelConfig(
            username="ahmet",
            password="admin-secret",
            password_hash_value=None,
            repository="ahmetelci2014-rgb/kriptosinyalanaliz63",
            ref="main",
            github_token="read-token",
            root=Path("."),
            refresh_seconds=30,
            cookie_secure=True,
            trust_proxy=True,
            session_hours=12,
        )

    def test_requires_separate_repository(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                isolated_account_store_from_env(self.config())

    def test_rejects_signal_repository(self):
        with mock.patch.dict(
            os.environ,
            {
                "PANEL_USERS_REPOSITORY": "ahmetelci2014-rgb/kriptosinyalanaliz63",
                "GITHUB_PANEL_USERS_TOKEN": "write-token",
            },
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                isolated_account_store_from_env(self.config())

    def test_uses_only_separate_users_repository(self):
        with mock.patch.dict(
            os.environ,
            {
                "PANEL_USERS_REPOSITORY": "ahmetelci2014-rgb/kripto-panel-users",
                "GITHUB_PANEL_USERS_TOKEN": "write-token",
                "PANEL_USERS_REF": "main",
                "PANEL_USERS_PATH": "panel_users.json",
            },
            clear=True,
        ):
            store = isolated_account_store_from_env(self.config())
            self.assertEqual(store.repository, "ahmetelci2014-rgb/kripto-panel-users")
            self.assertEqual(store.ref, "main")
            self.assertEqual(store.path, "panel_users.json")
            self.assertEqual(store.token, "write-token")
            self.assertNotEqual(store.repository, self.config().repository)


if __name__ == "__main__":
    unittest.main()
