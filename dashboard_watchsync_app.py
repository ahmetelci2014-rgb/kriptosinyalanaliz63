"""V3.32.8 hesaba bağlı İzleme Listesi yardımcıları.

Bu modül yalnız panel kullanıcı tercihlerini yönetir. Trading/sinyal/Telegram/TP-SL-BE
ve ledger/state dosyalarına dokunmaz. Yönetilen panel_users hesaplarında favori coin
listesini hesap tercihi olarak saklar; kurucu/ortam hesabında mevcut cihaz-local
fallback davranışı korunur.
"""
from __future__ import annotations

import html
import json
import time
from typing import Any

import dashboard_accounts_app as accounts
from dashboard_live_app import OKXMarketDataClient

MAX_WATCH = 12
PREF_KEY = "watchlist"


def normalize_symbol(value: Any) -> str:
    try:
        return OKXMarketDataClient.normalize_symbol(str(value or ""))
    except ValueError:
        return ""


def normalize_watchlist(values: Any) -> list[str]:
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    for raw in values:
        symbol = normalize_symbol(raw)
        if symbol and symbol not in result:
            result.append(symbol)
        if len(result) >= MAX_WATCH:
            break
    return result


def _raw_user(store, document: dict[str, Any], username: str) -> dict[str, Any]:
    finder = getattr(store, "_raw_user_unlocked", None)
    if callable(finder):
        return finder(document, username)
    finder = getattr(store, "_find_raw_user", None)
    if callable(finder):
        return finder(document, username)
    raise ValueError("Kullanıcı bulunamadı.")


def account_watchlist_snapshot(store, username: str) -> dict[str, Any]:
    """Hesap tercihindeki listeyi döndürür; kurucu/env hesapta managed=False."""
    if not store or not bool(getattr(store, "configured", False)):
        return {"managed": False, "initialized": False, "symbols": [], "updated_at": 0}
    try:
        with store._lock:
            _users, document, _sha = store._users_unlocked()
            raw = _raw_user(store, document, username)
            prefs = raw.get("preferences") if isinstance(raw.get("preferences"), dict) else {}
            initialized = PREF_KEY in prefs
            symbols = normalize_watchlist(prefs.get(PREF_KEY) or []) if initialized else []
            updated_at = int(prefs.get("watchlist_updated_at") or 0) if initialized else 0
    except (accounts.AccountStoreError, ValueError, TypeError):
        return {"managed": False, "initialized": False, "symbols": [], "updated_at": 0}
    return {
        "managed": True,
        "initialized": bool(initialized),
        "symbols": symbols,
        "updated_at": updated_at,
    }


def save_account_watchlist(store, username: str, values: Any, *, actor: str | None = None) -> list[str]:
    """Yalnız preferences.watchlist alanını yazar; üyelik/plan zamanlarını değiştirmez."""
    symbols = normalize_watchlist(values)
    if not store or not bool(getattr(store, "configured", False)):
        raise ValueError("Bu hesap sunucu kullanıcı deposunda yönetilmiyor.")
    with store._lock:
        _users, document, sha = store._users_unlocked()
        raw = _raw_user(store, document, username)
        prefs = raw.get("preferences")
        if not isinstance(prefs, dict):
            prefs = {}
            raw["preferences"] = prefs
        prefs[PREF_KEY] = symbols
        prefs["watchlist_updated_at"] = int(time.time())
        store._save_unlocked(
            document,
            sha,
            actor=str(actor or username or "member"),
            action=f"watchlist-sync {username}",
        )
    return symbols


def first_sync_list(server_symbols: Any, browser_symbols: Any) -> list[str]:
    """Yalnız ilk senkronizasyonda eski cihaz favorilerini kaybetmeden birleştirir."""
    return normalize_watchlist([*normalize_watchlist(server_symbols), *normalize_watchlist(browser_symbols)])


def enhance_mobile_watchlist_notice(body: str, *, managed: bool) -> str:
    if managed:
        text = "İzleme listen hesabına bağlıdır; telefon ve masaüstünde aynı liste kullanılır. Bu cihazdaki cookie yalnız geçiş/yedek amaçlı tutulur."
    else:
        text = "Bu kurucu/ortam hesabı panel_users deposunda yönetilmediği için İzleme Listesi yalnız bu cihazda saklanır."
    old = "Liste yalnız bu tarayıcıda tercih çereziyle saklanır. Teknik özet işlem sinyali veya başarı olasılığı değildir."
    new = html.escape(text) + " Teknik özet işlem sinyali veya başarı olasılığı değildir."
    return body.replace(old, new)


def desktop_sync_script(*, csrf: str, nonce: str | None) -> str:
    nonce_attr = f' nonce="{html.escape(str(nonce), quote=True)}"' if nonce else ""
    csrf_json = json.dumps(str(csrf or ""), ensure_ascii=False)
    return f'''<script id="v3328-watch-sync"{nonce_attr}>
(() => {{
  const API='/api/account/watchlist', FAV='kripto_focus_favs', CSRF={csrf_json};
  const normalize=v=>{{let s=String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'');if(s&&!s.endsWith('USDT'))s+='USDT';return s;}};
  const valid=s=>/^[A-Z0-9]{{2,15}}USDT$/.test(s);
  const clean=v=>{{const raw=Array.isArray(v)?v:[];return [...new Set(raw.map(normalize).filter(valid))].slice(0,12);}};
  const readLocal=()=>{{try{{return clean(JSON.parse(localStorage.getItem(FAV)||'[]'));}}catch{{return [];}}}};
  const writeLocal=list=>{{try{{localStorage.setItem(FAV,JSON.stringify(clean(list)));}}catch{{}}}};
  let managed=false, initialized=false, busy=false;
  async function post(list){{
    if(!managed||busy)return;busy=true;
    try{{
      const body=new URLSearchParams({{csrf:CSRF,symbols:clean(list).join(',')}});
      const r=await fetch(API,{{method:'POST',credentials:'same-origin',cache:'no-store',headers:{{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'}},body}});
      if(r.status===401){{location.assign('/login');return;}}
      if(!r.ok)return;
      const p=await r.json();if(p.managed){{initialized=true;writeLocal(p.symbols||[]);}}
    }}catch{{}}finally{{busy=false;}}
  }}
  async function hydrate(){{
    try{{
      const r=await fetch(API,{{credentials:'same-origin',cache:'no-store',headers:{{Accept:'application/json'}}}});
      if(r.status===401){{location.assign('/login');return;}}if(!r.ok)return;
      const p=await r.json();managed=Boolean(p.managed);initialized=Boolean(p.initialized);
      if(!managed)return;
      const server=clean(p.symbols||[]), local=readLocal();
      if(initialized){{writeLocal(server);}}
      else{{const merged=clean([...server,...local]);writeLocal(merged);await post(merged);}}
      setTimeout(()=>document.getElementById('watchRefreshBtn')?.click(),60);
    }}catch{{}}
  }}
  const pushSoon=()=>setTimeout(()=>post(readLocal()),180);
  document.addEventListener('click',e=>{{
    if(e.target.closest('[data-watch-add],[data-watch-remove],#watchAddBtn,#focusStar'))pushSoon();
  }});
  document.addEventListener('keydown',e=>{{if(e.key==='Enter'&&e.target?.id==='watchAddInput')pushSoon();}});
  window.addEventListener('storage',e=>{{if(e.key===FAV)pushSoon();}});
  hydrate();
}})();
</script>'''


def enhance_desktop_watch_sync(body: str, *, csrf: str, nonce: str | None) -> str:
    if 'id="v3328-watch-sync"' in body or 'id="page-watchlist"' not in body:
        return body
    body = body.replace(
        "RSI, EMA ve hacim yalnız OKX public 15m mumlarından hesaplanır. Bu ekran emir açmaz ve sinyal üretmez.",
        "RSI, EMA ve hacim yalnız OKX public 15m mumlarından hesaplanır. Yönetilen hesaplarda favoriler cihazlar arasında senkronlanır. Bu ekran emir açmaz ve sinyal üretmez.",
        1,
    )
    script = desktop_sync_script(csrf=csrf, nonce=nonce)
    return body.replace("</body>", script + "\n</body>", 1)
