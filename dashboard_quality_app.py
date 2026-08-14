"""Kripto Kontrol Merkezi V3.5 - ürün kalitesi ve satış dönüşümü.

V3.4 şeffaf performans katmanını koruyarak yalnız panel/ürün deneyimini geliştirir:
- herkese açık vitrinde canlı güven bandı, net FREE/PREMIUM karşılaştırması ve nasıl çalışır akışı,
- Premium paket adı/süre/fiyatının ortam ayarlarından halka açık güvenli biçimde gösterilmesi,
- o anda kaç açık işlemin FREE'de görünür, kaçının Premium'da kilitli olduğunun yalnız adet olarak gösterilmesi,
- mobilde hafif sabit kayıt/Premium çağrısı,
- FREE panelinde kullanım sınırı, veri yenilenme zamanı ve Premium farkının daha anlaşılır gösterilmesi.

Halka açık payload işlem seviyesi, kullanıcı bilgisi, ödeme talimatı, Entry/TP/SL veya skor içermez.
Sinyal üretimi, strateji, radarlar, Telegram ve emir akışı değişmez.
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_billing_app as billing
import dashboard_commercial_app as commercial
import dashboard_freepreview_app as freepreview
import dashboard_market_app as market
import dashboard_transparency_app as transparency
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_5_PRODUCT_QUALITY_2026_08_15"


def build_public_product(data: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Satış vitrini için hassas olmayan ürün durumu ve paket bilgisini üretir."""
    settings = dict(settings or billing._settings())
    summary = commercial.build_public_summary(data)
    open_count = max(0, commercial._int(summary.get("open_count"), 0))
    free_visible = 1 if open_count > 0 else 0
    recent = transparency.build_public_results(data)
    package_name = commercial._safe_text(settings.get("package_name"), 80) or "Premium"
    price_label = commercial._safe_text(settings.get("price_label"), 80) or "Fiyat bilgisi"
    days = max(1, min(commercial._int(settings.get("days"), 30), 3650))
    return {
        "version": VERSION,
        "system": {
            "health": summary.get("health", "UNKNOWN"),
            "open_count": open_count,
            "free_visible_open": free_visible,
            "premium_locked_open": max(0, open_count - free_visible),
            "recent_result_count": len(recent.get("items") or []),
            "tp_rate_percent": summary.get("tp_rate_percent"),
            "updated_at": int(time.time()),
        },
        "free": {
            "visible_open_signals": 1,
            "visible_results": 5,
            "market_symbols": len(freepreview.FREE_MARKET_SYMBOLS),
            "entry_tp1_sl": True,
            "tp2_tp3": False,
            "opportunity_center": False,
            "analysis_score": False,
            "audio_alerts": False,
        },
        "premium": {
            "package_name": package_name,
            "price_label": price_label,
            "days": days,
            "all_open_signals": True,
            "tp2_tp3": True,
            "opportunity_center": True,
            "analysis_score": True,
            "audio_alerts": True,
        },
        "disclaimer": "Canlı adetler ve geçmiş sonuçlar kazanç garantisi değildir.",
    }


def enhance_public_quality(body: str, nonce: str) -> str:
    """V3.4 açık vitrini daha net, profesyonel ve dönüşüm odaklı hale getirir."""
    body = body.replace('id="plans"', 'id="plansLegacy"', 1)
    css = r'''
#plansLegacy{display:none}.v35-livebar{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:-4px 0 24px}.v35-liveitem{border:1px solid var(--line);border-radius:13px;padding:12px 13px;background:rgba(8,23,30,.9)}.v35-liveitem small{display:block;color:#6d8784;font-size:8px;text-transform:uppercase;letter-spacing:.08em}.v35-liveitem b{display:block;margin-top:3px;font-size:15px}.v35-liveitem .ok{color:var(--green)}.v35-liveitem .premium{color:var(--amber)}.v35-process{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.v35-step{border:1px solid var(--line);border-radius:16px;padding:18px;background:#091820}.v35-stepnum{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;background:rgba(44,230,191,.09);border:1px solid rgba(44,230,191,.25);color:var(--teal);font-weight:950}.v35-step h3{margin:10px 0 5px}.v35-step p{margin:0;color:var(--muted);font-size:12px}.v35-compare{border:1px solid rgba(44,230,191,.22);border-radius:19px;overflow:hidden;background:#091820}.v35-planhead{display:grid;grid-template-columns:1.2fr .8fr .8fr;background:#0d2029;border-bottom:1px solid var(--line)}.v35-planhead>div{padding:16px}.v35-planhead strong{font-size:20px}.v35-price{color:var(--teal);font-size:17px;font-weight:950}.v35-price small{display:block;color:var(--muted);font-size:9px;font-weight:700}.v35-row{display:grid;grid-template-columns:1.2fr .8fr .8fr;border-bottom:1px solid rgba(27,57,67,.7)}.v35-row:last-child{border-bottom:0}.v35-row>div{padding:11px 16px}.v35-row>div:not(:first-child){text-align:center}.v35-row .label{color:#a8bbb8}.v35-yes{color:var(--green);font-weight:950}.v35-limited{color:var(--amber);font-weight:900}.v35-no{color:#617b78}.v35-plancta{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;margin-top:14px}.v35-trustnote{border-left:2px solid var(--teal);padding:2px 0 2px 12px;color:#7f9996;font-size:10px}.v35-faq{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.v35-faqitem{border:1px solid var(--line);border-radius:14px;padding:15px;background:#08171e}.v35-faqitem b{display:block;margin-bottom:5px}.v35-faqitem span{color:var(--muted);font-size:11px}.v35-mobile-cta{display:none}.v35-updated{color:#6f8986;font-size:9px;margin-top:8px}@media(max-width:800px){.v35-livebar{grid-template-columns:1fr 1fr}.v35-process,.v35-faq{grid-template-columns:1fr}.v35-planhead,.v35-row{grid-template-columns:1.2fr .8fr .8fr}.v35-planhead>div,.v35-row>div{padding:10px 8px;font-size:10px}.v35-planhead strong{font-size:15px}.v35-price{font-size:13px}.v35-mobile-cta{position:fixed;display:flex;left:8px;right:8px;bottom:8px;z-index:50;background:rgba(6,16,22,.94);backdrop-filter:blur(12px);border:1px solid var(--line);border-radius:14px;padding:7px;gap:7px;box-shadow:0 12px 40px rgba(0,0,0,.35)}.v35-mobile-cta .btn{flex:1}.foot{padding-bottom:95px}}@media(max-width:480px){.v35-livebar{grid-template-columns:1fr 1fr}.v35-row{grid-template-columns:1.15fr .75fr .75fr}}
'''
    if "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)

    livebar = r'''
<div class="v35-livebar">
  <div class="v35-liveitem"><small>Sistem durumu</small><b class="ok" id="v35Health">Canlı veri</b></div>
  <div class="v35-liveitem"><small>FREE canlı işlem</small><b id="v35FreeOpen">—</b></div>
  <div class="v35-liveitem"><small>Premium'da ek açık</small><b class="premium" id="v35LockedOpen">—</b></div>
  <div class="v35-liveitem"><small>Son gerçek sonuç</small><b id="v35ResultCount">—</b></div>
</div>
'''
    hero_close = "</section>\n<section class=\"section\"><h2>Önce sistemi gör, sonra karar ver.</h2>"
    if hero_close in body:
        body = body.replace(hero_close, "</section>\n" + livebar + '<section class="section"><h2>Önce sistemi gör, sonra karar ver.</h2>', 1)

    sections = r'''
<section class="section" id="how"><h2>Nasıl çalışır?</h2><p>Ürünü görmeden ödeme istemiyoruz. Önce gerçek veriyi incele, FREE ile dene, ihtiyacın varsa Premium'a geç.</p><div class="v35-process">
  <div class="v35-step"><div class="v35-stepnum">1</div><h3>Siteyi açık incele</h3><p>Genel canlı istatistikleri ve son gerçek sonuçları giriş yapmadan gör.</p></div>
  <div class="v35-step"><div class="v35-stepnum">2</div><h3>FREE ile gerçek sinyal dene</h3><p>1 gerçek açık işlemde coin, yön, Entry, TP1 ve SL seviyelerini takip et; son 5 sonucu gör.</p></div>
  <div class="v35-step"><div class="v35-stepnum">3</div><h3>İstersen Premium'a geç</h3><p>Tüm açık sinyaller, TP2/TP3, Fırsatlar, skorlar, sesli uyarılar ve detaylı analiz açılır.</p></div>
</div></section>
<section class="section" id="plans"><h2>FREE ve PREMIUM farkı</h2><p>Sınırlar net. FREE gerçekten kullanılabilir; Premium bütün analiz ve takip araçlarını açar.</p>
<div class="v35-compare"><div class="v35-planhead"><div><strong>Özellik</strong></div><div><strong>FREE</strong><small>Gerçek deneme</small></div><div><strong>PREMIUM</strong><small id="v35PackageName">Premium</small><div class="v35-price" id="v35Price">—</div></div></div>
<div class="v35-row"><div class="label">Gerçek açık sinyal</div><div class="v35-limited">1 adet</div><div class="v35-yes">Tümü</div></div>
<div class="v35-row"><div class="label">Entry + TP1 + SL</div><div class="v35-yes">✓</div><div class="v35-yes">✓</div></div>
<div class="v35-row"><div class="label">TP2 + TP3</div><div class="v35-no">—</div><div class="v35-yes">✓</div></div>
<div class="v35-row"><div class="label">Son gerçek sonuçlar</div><div class="v35-limited">5 kayıt</div><div class="v35-yes">Detaylı</div></div>
<div class="v35-row"><div class="label">Canlı temel piyasa</div><div class="v35-limited">6 coin</div><div class="v35-yes">Geniş</div></div>
<div class="v35-row"><div class="label">Fırsat Merkezi + filtreler</div><div class="v35-no">—</div><div class="v35-yes">✓</div></div>
<div class="v35-row"><div class="label">Teknik İnceleme Skoru</div><div class="v35-no">—</div><div class="v35-yes">✓</div></div>
<div class="v35-row"><div class="label">Sesli / renkli canlı uyarı</div><div class="v35-no">—</div><div class="v35-yes">✓</div></div>
<div class="v35-row"><div class="label">İzleme Listesi + detaylı performans</div><div class="v35-no">—</div><div class="v35-yes">✓</div></div>
</div><div class="v35-plancta"><div><b id="v35PackageLine">Premium paket bilgisi yükleniyor…</b><div class="v35-updated" id="v35Updated"></div></div><div class="hero-actions"><a class="btn" href="/register">FREE hesap aç</a><a class="btn primary" href="/login">Premium'a geç / giriş</a></div></div>
<p class="v35-trustnote">Sistem otomatik emir açmaz, kullanıcı parasını tutmaz ve kazanç garantisi vermez. Gösterilen sonuçlar geçmiş sistem kayıtlarıdır.</p></section>
<section class="section"><h2>Sık sorulan 3 soru</h2><div class="v35-faq"><div class="v35-faqitem"><b>FREE gerçekten gerçek işlem gösteriyor mu?</b><span>Evet. O anda sistemdeki en yeni uygun açık işlemlerden 1 tanesi Entry, TP1 ve SL ile gösterilir; sonradan kazanan işlem seçilmez.</span></div><div class="v35-faqitem"><b>Premium satın alınca otomatik işlem açılır mı?</b><span>Hayır. Panel analiz ve takip yazılımıdır. Borsa hesabında otomatik emir açmaz.</span></div><div class="v35-faqitem"><b>Sonuçlar sadece TP olanlardan mı seçiliyor?</b><span>Hayır. TP, SL ve BE sonuçları kronolojik akıştan gösterilir; yalnız kazananları seçme yapılmaz.</span></div></div></section>
<div class="v35-mobile-cta"><a class="btn" href="/register">FREE Dene</a><a class="btn primary" href="#plans">Premium</a></div>
'''
    marker = '<footer class="foot">'
    if marker in body:
        body = body.replace(marker, sections + marker, 1)
    else:
        body = body.replace("</body>", sections + "</body>", 1)

    nonce_attr = html.escape(nonce, quote=True)
    script = f'''<script nonce="{nonce_attr}">(()=>{{
const fmtTime=v=>{{const n=Number(v);return Number.isFinite(n)&&n>0?new Date(n*1000).toLocaleTimeString('tr-TR',{{hour:'2-digit',minute:'2-digit'}}):'—';}};
async function loadProduct(){{try{{const r=await fetch('/api/public/product',{{cache:'no-store'}}),d=await r.json();if(!r.ok)return;const s=d.system||{{}},p=d.premium||{{}};const health=document.getElementById('v35Health');if(health)health.textContent=s.health==='GREEN'?'Sistem çalışıyor':s.health==='YELLOW'?'Kontrol ediliyor':s.health==='RED'?'Veri uyarısı':'Canlı veri';const f=document.getElementById('v35FreeOpen');if(f)f.textContent=s.free_visible_open?String(s.free_visible_open)+' açık':'Yeni sinyal bekleniyor';const l=document.getElementById('v35LockedOpen');if(l)l.textContent=String(s.premium_locked_open??0);const c=document.getElementById('v35ResultCount');if(c)c.textContent=String(s.recent_result_count??0)+' kayıt';const name=document.getElementById('v35PackageName');if(name)name.textContent=p.package_name||'Premium';const price=document.getElementById('v35Price');if(price)price.innerHTML=(p.price_label||'Fiyat bilgisi')+`<small>${{Number(p.days||30)}} gün erişim</small>`;const line=document.getElementById('v35PackageLine');if(line)line.textContent=`${{p.package_name||'Premium'}} · ${{p.price_label||'Fiyat bilgisi'}} · ${{Number(p.days||30)}} gün`;const u=document.getElementById('v35Updated');if(u)u.textContent='Canlı ürün durumu · '+fmtTime(s.updated_at);}}catch{{}}}}
loadProduct();setInterval(loadProduct,30000);
}})();</script>'''
    return body.replace("</body>", script + "\n</body>", 1)


def enhance_free_quality(body: str, nonce: str) -> str:
    """FREE panelin sınırlarını ve Premium farkını daha anlaşılır gösterir."""
    css = r'''
.v35-freebar{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:10px 0 0}.v35-freeitem{border:1px solid var(--line);border-radius:10px;padding:9px 10px;background:#08171e}.v35-freeitem small{display:block;color:#6d8784;font-size:7px;text-transform:uppercase}.v35-freeitem b{display:block;margin-top:2px}.v35-freeitem.premium b{color:var(--amber)}.v35-freequality{margin-top:11px;border:1px solid rgba(105,174,248,.2);border-radius:14px;padding:14px;background:rgba(105,174,248,.035)}.v35-freequality-head{display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap}.v35-freequality-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:10px}.v35-qfeature{border:1px solid var(--line);border-radius:10px;padding:10px;background:#07151c}.v35-qfeature b{display:block;font-size:10px}.v35-qfeature small{display:block;color:var(--muted);margin-top:3px}.v35-refresh{color:#718b88;font-size:9px}.v35-free-mobile{display:none}@media(max-width:800px){.v35-freequality-grid{grid-template-columns:1fr 1fr}}@media(max-width:520px){.v35-freebar{grid-template-columns:1fr 1fr}.v35-free-mobile{position:fixed;display:flex;left:8px;right:8px;bottom:8px;z-index:60;background:rgba(6,16,22,.95);border:1px solid var(--line);border-radius:13px;padding:7px;box-shadow:0 12px 35px rgba(0,0,0,.35)}.v35-free-mobile .btn{width:100%}.shell{padding-bottom:88px}}
'''
    if "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)
    bar = r'''
<div class="v35-freebar"><div class="v35-freeitem"><small>FREE hakkın</small><b>1 gerçek sinyal</b></div><div class="v35-freeitem"><small>Sonuç geçmişi</small><b>Son 5 kayıt</b></div><div class="v35-freeitem"><small>Piyasa</small><b>6 canlı coin</b></div><div class="v35-freeitem premium"><small>Premium kilitli</small><b id="v35FreeLocked">—</b></div></div>
'''
    hero_end = "</section>\n<div class=\"grid\">"
    if hero_end in body:
        body = body.replace(hero_end, "</section>" + bar + "\n<div class=\"grid\">", 1)
    quality = r'''
<section class="v35-freequality"><div class="v35-freequality-head"><div><h2>FREE ile gerçek sistemi ölç</h2><p>Gösterilen işlem kapanınca sonucu da takip et. Premium'a geçmeden önce ürünün çalışma biçimini gör.</p></div><span class="v35-refresh" id="v35FreeUpdated">Veri yenileniyor…</span></div><div class="v35-freequality-grid"><div class="v35-qfeature"><b>✓ Gerçek sinyal</b><small>Coin + yön + Entry + TP1 + SL</small></div><div class="v35-qfeature"><b>✓ Şeffaf sonuç</b><small>TP, SL ve BE kronolojik görünür</small></div><div class="v35-qfeature"><b>🔒 Gelişmiş hedefler</b><small>TP2 + TP3 Premium'da</small></div><div class="v35-qfeature"><b>🔒 Analiz araçları</b><small>Fırsatlar, skor, sesli uyarı Premium'da</small></div></div></section><div class="v35-free-mobile"><a class="btn primary" href="/premium">Premium özellikleri aç</a></div>
'''
    marker = '<div class="free-note">'
    if marker in body:
        body = body.replace(marker, quality + marker, 1)
    dispatch_from = "window.dispatchEvent(new CustomEvent('kripto-free-preview',{detail:d}));"
    if dispatch_from not in body:
        dispatch_from = "renderSignal(d);renderResults(d.recent_results);"
        dispatch_to = "renderSignal(d);renderResults(d.recent_results);window.dispatchEvent(new CustomEvent('kripto-free-preview',{detail:d}));"
        if dispatch_from in body:
            body = body.replace(dispatch_from, dispatch_to, 1)
    nonce_attr = html.escape(nonce, quote=True)
    listener = f'''<script nonce="{nonce_attr}">(()=>{{
window.addEventListener('kripto-free-preview',e=>{{const d=e.detail||{{}},locked=document.getElementById('v35FreeLocked'),u=document.getElementById('v35FreeUpdated');if(locked)locked.textContent=Number(d.locked_open_count||0)>0?String(d.locked_open_count)+' ek sinyal':'0 ek sinyal';if(u){{const n=Number(d.updated_at);u.textContent=Number.isFinite(n)&&n>0?'Son yenileme '+new Date(n*1000).toLocaleTimeString('tr-TR',{{hour:'2-digit',minute:'2-digit'}}):'Canlı veri';}}}});
}})();</script>'''
    script_marker = '<script nonce="'
    pos = body.find(script_marker)
    if pos >= 0:
        body = body[:pos] + listener + "\n" + body[pos:]
    else:
        body = body.replace("</body>", listener + "\n</body>", 1)
    return body


def make_v35_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    BaseHandler = transparency.make_v34_handler(config, service, sessions, limiter, store, market_client, overview_client)
    settings = billing._settings()

    class V35Handler(BaseHandler):
        server_version = "KriptoPanel/3.5"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            info = self._plan_info(session)
            if str(info.get("plan")) != commercial.PLAN_FREE:
                return super()._render_root_v17(session)
            nonce = secrets.token_urlsafe(18)
            body = freepreview.free_preview_page(session, nonce)
            body = transparency.enhance_free_page(body, nonce)
            body = enhance_free_quality(body, nonce)
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "public_product_quality": True,
                    "free_quality": True,
                    "signal_engine": "unchanged",
                })
                return
            if path == "/api/public/product":
                try:
                    payload = build_public_product(service.get_data(), settings)
                except Exception:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": "public_product_unavailable"})
                    return
                self._json(HTTPStatus.OK, payload)
                return
            if path == "/" and not self._session():
                nonce = secrets.token_urlsafe(18)
                body = commercial.public_home_page(nonce)
                body = transparency.enhance_public_home(body, nonce)
                body = enhance_public_quality(body, nonce)
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)
                return
            return super().do_GET()

    return V35Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.5 ürün kalitesi ve satış dönüşümü.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = commercial.commercial_store_from_env(config)
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v35_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} product_quality=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
