from __future__ import annotations

import copy
import inspect
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_app as app
import dashboard_commercial_app as commercial
import dashboard_watchsync_app as sync
import dashboard_watchsync_runtime_app as runtime


class MemoryCommercialStore(commercial.CommercialAccountStore):
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


class WatchlistSyncTests(unittest.TestCase):
    def make_store(self):
        store = MemoryCommercialStore()
        store.create_free_user("uye01", "member-secret-123", actor="test")
        return store

    def test_first_sync_preserves_existing_browser_favorites_once(self):
        store = self.make_store()
        first = sync.account_watchlist_snapshot(store, "uye01")
        self.assertTrue(first["managed"])
        self.assertFalse(first["initialized"])
        merged = sync.first_sync_list([], ["btc", "ETHUSDT", "btc", "bad!"])
        self.assertEqual(merged, ["BTCUSDT", "ETHUSDT", "BADUSDT"])
        saved = sync.save_account_watchlist(store, "uye01", merged, actor="uye01")
        self.assertEqual(saved, merged)
        current = sync.account_watchlist_snapshot(store, "uye01")
        self.assertTrue(current["initialized"])
        self.assertEqual(current["symbols"], merged)

    def test_initialized_account_is_authoritative_and_removed_symbol_stays_removed(self):
        store = self.make_store()
        sync.save_account_watchlist(store, "uye01", ["BTCUSDT", "ETHUSDT"], actor="uye01")
        sync.save_account_watchlist(store, "uye01", ["ETHUSDT"], actor="uye01")
        current = sync.account_watchlist_snapshot(store, "uye01")
        self.assertEqual(current["symbols"], ["ETHUSDT"])
        self.assertNotIn("BTCUSDT", current["symbols"])
        raw = store.document["users"][0]
        self.assertIn("preferences", raw)
        self.assertNotIn("preferences", store.list_commercial_users()[0])

    def test_unmanaged_account_falls_back_without_writing(self):
        store = self.make_store()
        snapshot = sync.account_watchlist_snapshot(store, "kurucu-env")
        self.assertFalse(snapshot["managed"])
        before = copy.deepcopy(store.document)
        with self.assertRaises(ValueError):
            sync.save_account_watchlist(store, "kurucu-env", ["BTCUSDT"])
        self.assertEqual(store.document, before)

    def test_watchlist_is_bounded_deduplicated_and_normalized(self):
        values = ["btc", "BTCUSDT", "eth", *[f"X{i}USDT" for i in range(30)]]
        result = sync.normalize_watchlist(values)
        self.assertEqual(result[:2], ["BTCUSDT", "ETHUSDT"])
        self.assertLessEqual(len(result), sync.MAX_WATCH)
        self.assertEqual(len(result), len(set(result)))

    def test_mobile_copy_distinguishes_account_sync_and_device_fallback(self):
        raw = "<div>Liste yalnız bu tarayıcıda tercih çereziyle saklanır. Teknik özet işlem sinyali veya başarı olasılığı değildir.</div>"
        managed = sync.enhance_mobile_watchlist_notice(raw, managed=True)
        fallback = sync.enhance_mobile_watchlist_notice(raw, managed=False)
        self.assertIn("telefon ve masaüstünde aynı liste", managed)
        self.assertIn("yalnız bu cihazda", fallback)

    def test_desktop_sync_is_only_added_to_real_watchlist_surface(self):
        page = '<html><body><section id="page-watchlist"></section><div>RSI, EMA ve hacim yalnız OKX public 15m mumlarından hesaplanır. Bu ekran emir açmaz ve sinyal üretmez.</div></body></html>'
        enhanced = sync.enhance_desktop_watch_sync(page, csrf="csrf-1", nonce="nonce-1")
        self.assertIn('id="v3328-watch-sync"', enhanced)
        self.assertIn("/api/account/watchlist", enhanced)
        self.assertIn("kripto_focus_favs", enhanced)
        self.assertIn("cihazlar arasında senkronlanır", enhanced)
        self.assertEqual(sync.enhance_desktop_watch_sync(enhanced, csrf="x", nonce="y"), enhanced)
        self.assertEqual(sync.enhance_desktop_watch_sync("<html></html>", csrf="x", nonce="y"), "<html></html>")

    def test_desktop_sync_javascript_has_valid_syntax_when_node_exists(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        html = sync.desktop_sync_script(csrf="csrf-test", nonce="nonce-test")
        code = html.split(">", 1)[1].rsplit("</script>", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchsync.js"
            path.write_text(code, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_wraps_v3327_and_exposes_only_panel_preference_sync(self):
        source = inspect.getsource(runtime)
        helper = inspect.getsource(sync)
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_watchsync_runtime_app")
        self.assertEqual(app.VERSION, runtime.VERSION)
        self.assertIn("V3_32_8_WATCHLIST_SYNC", runtime.VERSION)
        self.assertIn("previous.make_v3321_handler", source)
        self.assertIn('"watchlist_sync": "managed_account_cross_device"', source)
        self.assertIn('path == "/api/account/watchlist"', source)
        self.assertIn('path == "/mobile/watchlist"', source)
        for forbidden in ("strategy.py", "config.py", "trade_ledger.json", "open_signals.json"):
            self.assertNotIn(forbidden, source)
            self.assertNotIn(forbidden, helper)

    def test_docker_includes_watchsync_modules(self):
        docker = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
        ignore = Path(".dockerignore").read_text(encoding="utf-8")
        self.assertIn("dashboard_watchsync_app.py", docker)
        self.assertIn("dashboard_watchsync_runtime_app.py", docker)
        self.assertIn("!dashboard_watchsync_app.py", ignore)
        self.assertIn("!dashboard_watchsync_runtime_app.py", ignore)


if __name__ == "__main__":
    unittest.main()
