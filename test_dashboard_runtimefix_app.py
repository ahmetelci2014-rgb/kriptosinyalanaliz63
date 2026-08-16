from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_app as app
import dashboard_commercial_app as commercial
import dashboard_home_app as home
import dashboard_runtimefix_app as fix


class DashboardRuntimeFixTests(unittest.TestCase):
    def test_premium_classic_page_gets_independent_runtime_repair(self):
        body = home.home_dashboard_page(
            {"username": "premium", "role": "MEMBER", "csrf": "csrf"},
            "nonce-test",
        )
        repaired = fix.enhance_runtime_repair(body, "nonce-test")
        self.assertIn('id="v3321-runtime-repair-script"', repaired)
        self.assertIn("fetch('/api/dashboard'", repaired)
        self.assertIn("event.stopImmediatePropagation()", repaired)
        self.assertIn("document.querySelectorAll('.page')", repaired)
        self.assertIn("$('signalSearch')?.value", repaired)
        self.assertIn("$('refreshBtn')?.addEventListener", repaired)

    def test_free_page_remains_separate_and_does_not_get_premium_api(self):
        free = commercial.free_member_page(
            {"username": "free", "role": "MEMBER", "csrf": "csrf"},
            {"plan": commercial.PLAN_FREE, "plan_label": "Ücretsiz", "expires_at": None},
            "nonce-free",
        )
        repaired = fix.enhance_runtime_repair(free, "nonce-free")
        self.assertEqual(repaired, free)
        self.assertNotIn('id="v3321-runtime-repair-script"', repaired)
        self.assertNotIn("/api/dashboard", repaired)
        self.assertIn("/api/public/summary", repaired)

    def _runtime_javascript(self) -> str:
        html = fix.SCRIPT.replace("__NONCE__", "nonce-test")
        match = re.search(r"<script[^>]*>(.*)</script>", html, flags=re.S)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_runtime_script_has_valid_javascript_syntax_when_node_exists(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtimefix.js"
            path.write_text(self._runtime_javascript(), encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_script_loads_data_and_switches_views_when_node_exists(self):
        """Temel DOM/fetch davranışını Node içinde gerçekten çalıştırır."""
        if not shutil.which("node"):
            self.skipTest("node not installed")

        runtime_js = json.dumps(self._runtime_javascript())
        harness = r'''
const assert = require('assert');

function makeClassList(initial=false) {
  const set = new Set(initial ? ['active'] : []);
  return {
    toggle(name, force) {
      const on = force === undefined ? !set.has(name) : Boolean(force);
      if (on) set.add(name); else set.delete(name);
      return on;
    },
    contains(name) { return set.has(name); },
    add(name) { set.add(name); },
    remove(name) { set.delete(name); }
  };
}

function makeEl(id, dataset={}) {
  return {
    id,
    dataset,
    value: '',
    innerHTML: '',
    textContent: '',
    classList: makeClassList(id === 'page-home'),
    listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    closest() { return null; }
  };
}

const ids = new Map();
[
  'page-home','page-signals','page-trades','page-results','homeSmartMetrics',
  'homeStrongSignals','homeTodayFlow','signalsList','tradesList','resultsList',
  'signalSearch','signalDirection','signalSystem','resultSearch','resultOutcome',
  'refreshBtn','liveText','topTitle'
].forEach(id => ids.set(id, makeEl(id)));

const pages = ['home','signals','trades','results'].map(view => ids.get(`page-${view}`));
const navs = ['home','signals','trades','results'].map(view => {
  const el = makeEl(`nav-${view}`, {view});
  el.classList = makeClassList(view === 'home');
  return el;
});

const documentListeners = {};
const dispatched = [];
const fetched = [];
let assigned = null;

global.document = {
  documentElement: {dataset: {admin: 'false'}},
  getElementById(id) { return ids.get(id) || null; },
  querySelectorAll(selector) {
    if (selector === '.page') return pages;
    if (selector === '[data-view]') return navs;
    return [];
  },
  addEventListener(type, fn) { documentListeners[type] = fn; }
};

global.window = {
  __v3321RuntimeRepair: false,
  dispatchEvent(event) { dispatched.push(event); return true; },
  scrollTo() {}
};
global.CustomEvent = class CustomEvent {
  constructor(type, init={}) { this.type = type; this.detail = init.detail; }
};
global.location = {assign(url) { assigned = url; }};
global.setInterval = () => 1;
global.fetch = async (url) => {
  fetched.push(String(url));
  return {
    status: 200,
    ok: true,
    async json() {
      return {
        open_trades: [{symbol:'BTCUSDT',system_label:'Premium',direction:'LONG',entry:100,tp1:102,tp2:104,tp3:106,sl:98}],
        recent_results: [{symbol:'ETHUSDT',system_label:'Scalp',direction:'SHORT',outcome:'TP1',r_result:1}],
        data_quality: {ok:true}
      };
    }
  };
};

(async () => {
  eval(__RUNTIME_JS__);
  await new Promise(resolve => setTimeout(resolve, 0));

  assert.strictEqual(assigned, null, 'runtime unexpectedly redirected');
  assert.ok(fetched.some(url => url.startsWith('/api/dashboard')), 'dashboard API was not requested');
  assert.ok(ids.get('signalsList').innerHTML.includes('BTCUSDT'), 'open signal data was not rendered');
  assert.ok(ids.get('tradesList').innerHTML.includes('BTCUSDT'), 'trade data was not rendered');
  assert.ok(ids.get('resultsList').innerHTML.includes('ETHUSDT'), 'result data was not rendered');
  assert.strictEqual(ids.get('liveText').textContent, 'Canlı veri');
  assert.ok(dispatched.some(event => event.type === 'kripto-dashboard-data'), 'dashboard data event was not dispatched');

  const click = documentListeners.click;
  assert.ok(click, 'navigation click listener was not registered');
  click({
    target: {closest(selector) { return selector === '[data-view]' ? navs[1] : null; }},
    preventDefault() {},
    stopImmediatePropagation() {}
  });
  assert.strictEqual(pages[0].classList.contains('active'), false, 'home stayed active');
  assert.strictEqual(pages[1].classList.contains('active'), true, 'signals view did not activate');
  assert.strictEqual(navs[1].classList.contains('active'), true, 'signals nav did not activate');
  assert.strictEqual(ids.get('topTitle').textContent, 'Sinyaller');
})().catch(error => { console.error(error); process.exit(1); });
'''.replace("__RUNTIME_JS__", runtime_js)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtimefix_behavior.js"
            path.write_text(harness, encoding="utf-8")
            result = subprocess.run(["node", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_stable_entrypoint_uses_runtime_repair(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_runtimefix_app")
        self.assertEqual(app.VERSION, fix.VERSION)
        self.assertIs(app.make_handler, fix.make_v3321_handler)

    def test_runtime_repair_is_presentation_only(self):
        source = Path("dashboard_runtimefix_app.py").read_text(encoding="utf-8")
        self.assertNotIn("def do_POST", source)
        self.assertIn("v332.make_v332_handler", source)
        self.assertIn('"free_runtime":"separate_preserved"', source)
        self.assertIn('"signal_engine":"unchanged"', source)
        self.assertIn('"telegram":"unchanged"', source)
        self.assertIn('"trade_management":"unchanged"', source)
        self.assertIn('"ledger_write":"unchanged"', source)


if __name__ == "__main__":
    unittest.main()
