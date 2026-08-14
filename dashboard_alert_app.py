"""Kripto Kontrol Merkezi V2.4 - sesli/renkli canlı uyarı ve anlamlı renk katmanı.

V2.3 Bildirim Merkezi korunur. Bu dosya yalnız panel tarafında:
- yeni sinyal / TP / SL / BE için renkli toast uyarısı,
- kullanıcı açarsa Web Audio ile kısa ses uyarısı,
- ekranların anlamına göre ölçülü renk ayrımı
sağlar.

Sinyal üretimi, Telegram, strateji ve emir akışı bu dosyada yoktur.
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_notify_app as notify
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_4_ALERT_THEME_2026_08_14"


def alert_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = notify.notification_dashboard_page(session, nonce)
    nonce_attr = html.escape(nonce, quote=True)

    extra_css = r'''
    /* V2.4: renk, ekranın anlamını güçlendirsin; veri yoğunluğunu artırmasın. */
    .sound-toggle{height:35px;border:1px solid var(--line);background:#0b1720;color:#91aaa7;border-radius:10px;padding:0 10px;font-size:9px;font-weight:900;white-space:nowrap}.sound-toggle.on{border-color:rgba(66,226,140,.45);background:rgba(66,226,140,.08);color:var(--green)}.sound-toggle.attention{animation:soundPulse 1.4s ease-in-out infinite}@keyframes soundPulse{50%{box-shadow:0 0 0 5px rgba(44,230,191,.08)}}
    .alert-stack{position:fixed;right:18px;top:72px;z-index:110;width:min(390px,calc(100vw - 28px));display:flex;flex-direction:column;gap:8px;pointer-events:none}.alert-toast{pointer-events:auto;border:1px solid var(--line);background:#0a1720;border-radius:13px;padding:11px 12px;box-shadow:0 18px 50px rgba(0,0,0,.36);display:grid;grid-template-columns:34px 1fr auto;gap:9px;align-items:center;animation:alertIn .22s ease-out both;overflow:hidden;position:relative}.alert-toast:before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--alert-color,var(--teal))}.alert-toast.signal-long{--alert-color:#42e28c;background:linear-gradient(90deg,rgba(66,226,140,.09),#0a1720 28%)}.alert-toast.signal-short{--alert-color:#ff6f91;background:linear-gradient(90deg,rgba(255,111,145,.09),#0a1720 28%)}.alert-toast.tp{--alert-color:#42e28c;background:linear-gradient(90deg,rgba(66,226,140,.10),#0a1720 28%)}.alert-toast.sl{--alert-color:#ff627d;background:linear-gradient(90deg,rgba(255,98,125,.10),#0a1720 28%)}.alert-toast.be{--alert-color:#ffbd59;background:linear-gradient(90deg,rgba(255,189,89,.10),#0a1720 28%)}.alert-icon{width:32px;height:32px;border-radius:9px;border:1px solid color-mix(in srgb,var(--alert-color) 45%,transparent);display:grid;place-items:center;color:var(--alert-color);font-size:14px;background:rgba(255,255,255,.025)}.alert-main{min-width:0}.alert-main strong{display:block;font-size:11px}.alert-main small{display:block;margin-top:2px;color:var(--muted);font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.alert-close{border:0;background:transparent;color:#78918e;font-size:16px;line-height:1;padding:5px;cursor:pointer}@keyframes alertIn{from{opacity:0;transform:translateY(-8px) translateX(12px)}to{opacity:1;transform:none}}@keyframes alertOut{to{opacity:0;transform:translateX(18px)}}
    /* Bölüm renk kodları */
    .nav-item[data-view="home"].active{background:rgba(44,230,191,.09);color:var(--teal);border-color:rgba(44,230,191,.18)}
    .nav-item[data-view="signals"].active{background:rgba(77,163,255,.10);color:#7db8ff;border-color:rgba(77,163,255,.20)}
    .nav-item[data-view="trades"].active{background:rgba(178,126,255,.10);color:#c39aff;border-color:rgba(178,126,255,.20)}
    .nav-item[data-view="results"].active{background:rgba(66,226,140,.09);color:var(--green);border-color:rgba(66,226,140,.20)}
    .nav-item[data-view="system"].active{background:rgba(255,157,85,.10);color:#ffad72;border-color:rgba(255,157,85,.22)}
    #page-home .panel{border-color:rgba(44,230,191,.13)}#page-home .panel-head{background:linear-gradient(90deg,rgba(44,230,191,.04),transparent 38%)}
    #page-signals .panel{border-color:rgba(77,163,255,.16)}#page-signals .panel-head{background:linear-gradient(90deg,rgba(77,163,255,.055),transparent 38%)}
    #page-trades .panel{border-color:rgba(178,126,255,.15)}#page-trades .panel-head{background:linear-gradient(90deg,rgba(178,126,255,.05),transparent 38%)}
    #page-results .panel{border-color:rgba(66,226,140,.14)}#page-results .panel-head{background:linear-gradient(90deg,rgba(66,226,140,.05),transparent 38%)}
    #page-system .panel{border-color:rgba(255,157,85,.16)}#page-system .panel-head{background:linear-gradient(90deg,rgba(255,157,85,.05),transparent 38%)}
    .tag.long{box-shadow:inset 0 0 0 1px rgba(66,226,140,.10)}.tag.short{box-shadow:inset 0 0 0 1px rgba(255,98,125,.12)}
    .metric.green{background:linear-gradient(145deg,rgba(66,226,140,.08),rgba(11,27,35,.94))}.metric.red{background:linear-gradient(145deg,rgba(255,98,125,.08),rgba(11,27,35,.94))}.metric.blue{background:linear-gradient(145deg,rgba(77,163,255,.08),rgba(11,27,35,.94))}
    @media(max-width:760px){.sound-toggle{width:34px;padding:0;font-size:0}.sound-toggle:before{content:'♪';font-size:13px}.sound-toggle.on:before{content:'♫'}.alert-stack{top:auto;bottom:78px;right:10px;width:calc(100vw - 20px)}}
    @media(prefers-reduced-motion:reduce){.alert-toast,.sound-toggle.attention{animation:none!important}}
    '''
    body = body.replace("  </style>", extra_css + "\n  </style>", 1)

    sound_button = '<button class="sound-toggle" id="soundToggle" type="button" title="Sesli uyarı">🔇 Ses Aç</button>'
    bell_anchor = '<button class="notify-trigger" id="notifyTrigger" type="button" aria-label="Bildirimler" title="Bildirimler">'
    if bell_anchor not in body:
        raise RuntimeError("V2.3 bildirim düğmesi bulunamadı.")
    body = body.replace(bell_anchor, sound_button + bell_anchor, 1)

    stack = '<div class="alert-stack" id="alertStack" aria-live="polite" aria-atomic="false"></div>'

    script = rf'''
<script nonce="{nonce_attr}">
(() => {{
  const $=id=>document.getElementById(id),stack=$('alertStack'),toggle=$('soundToggle');
  const SOUND='kripto_alert_sound_v24',SEEN='kripto_alert_seen_v24',INIT='kripto_alert_initialized_v24';
  let audio=null,armed=false;
  const normalize=v=>String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
  const system=r=>String(r?.system_label||r?.system||r?.source||'Sistem');
  const direction=r=>String(r?.direction||'').toUpperCase();
  const outcome=r=>String(r?.outcome||r?.result||'').toUpperCase();
  const parseTs=v=>{{if(v===null||v===undefined||v==='')return 0;if(typeof v==='number'||/^\d+(\.\d+)?$/.test(String(v))){{let n=Number(v);if(!Number.isFinite(n))return 0;if(n>1e12)n/=1000;return Math.round(n);}}const d=new Date(v);return Number.isNaN(d.getTime())?0:Math.round(d.getTime()/1000);}};
  const signalTs=r=>parseTs(r?.opened_at||r?.sent_at||r?.created_at||r?.detected_at||r?.updated_at);
  const resultTs=r=>parseTs(r?.closed_at||r?.finalized_at||r?.updated_at||r?.opened_at||r?.sent_at);
  const esc=v=>String(v??'').replace(/[&<>\"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[ch]));
  function isSoundOn(){{try{{return localStorage.getItem(SOUND)==='1';}}catch{{return false;}}}}
  function setSound(on){{try{{localStorage.setItem(SOUND,on?'1':'0');}}catch{{}}toggle.classList.toggle('on',on);toggle.classList.toggle('attention',on&&!armed);toggle.innerHTML=on?'🔊 Ses Açık':'🔇 Ses Aç';toggle.title=on?(armed?'Sesli uyarı açık':'Ses açık · ilk tıklamada etkinleşir'):'Sesli uyarı kapalı';}}
  function seenSet(){{try{{const raw=JSON.parse(localStorage.getItem(SEEN)||'[]');return new Set(Array.isArray(raw)?raw:[]);}}catch{{return new Set();}}}}
  function saveSeen(set){{try{{localStorage.setItem(SEEN,JSON.stringify([...set].slice(-800)));}}catch{{}}}}
  function key(type,row,ts){{return [type,normalize(row?.symbol),system(row),direction(row)||outcome(row),ts].join('|');}}
  function buildEvents(data){{const out=[];const open=Array.isArray(data?.open_trades)?data.open_trades:[],results=Array.isArray(data?.recent_results)?data.recent_results:[];open.forEach(r=>{{const symbol=normalize(r.symbol),ts=signalTs(r);if(symbol)out.push({{id:key('signal',r,ts),type:'signal',symbol,dir:direction(r),system:system(r),ts}});}});results.forEach(r=>{{const symbol=normalize(r.symbol),ts=resultTs(r),o=outcome(r)||'KAPALI';if(symbol)out.push({{id:key('result',r,ts),type:'result',symbol,outcome:o,dir:direction(r),system:system(r),ts}});}});return out.sort((a,b)=>b.ts-a.ts).slice(0,100);}}
  function eventKind(e){{if(e.type==='signal')return e.dir==='SHORT'?'signal-short':'signal-long';const o=String(e.outcome||'').toUpperCase();if(o.startsWith('TP'))return 'tp';if(o==='SL')return 'sl';if(o.includes('BE'))return 'be';return 'tp';}}
  function eventText(e){{if(e.type==='signal')return {{title:`${{e.symbol}} · ${{e.dir||'YENİ'}} SİNYAL`,detail:e.system,icon:'⚡'}};const k=eventKind(e);return {{title:`${{e.symbol}} · ${{e.outcome||'SONUÇ'}}`,detail:`${{e.system}} · ${{e.dir||'İşlem'}}`,icon:k==='tp'?'✓':k==='sl'?'×':k==='be'?'↔':'•'}};}}
  function ensureAudio(){{if(!isSoundOn())return null;const Ctx=window.AudioContext||window.webkitAudioContext;if(!Ctx)return null;try{{audio=audio||new Ctx();if(audio.state==='suspended')audio.resume();armed=true;setSound(true);return audio;}}catch{{return null;}}}}
  function tone(freq,start,duration,gain=.055){{const ctx=ensureAudio();if(!ctx||ctx.state!=='running')return;const osc=ctx.createOscillator(),g=ctx.createGain();osc.type='sine';osc.frequency.value=freq;g.gain.setValueAtTime(0.0001,ctx.currentTime+start);g.gain.exponentialRampToValueAtTime(gain,ctx.currentTime+start+.015);g.gain.exponentialRampToValueAtTime(0.0001,ctx.currentTime+start+duration);osc.connect(g);g.connect(ctx.destination);osc.start(ctx.currentTime+start);osc.stop(ctx.currentTime+start+duration+.02);}}
  function play(kind){{if(!isSoundOn())return;if(kind==='tp'){{tone(720,0,.13);tone(940,.14,.16);}}else if(kind==='sl'){{tone(330,0,.17);tone(220,.18,.22);}}else if(kind==='be'){{tone(520,0,.11);tone(520,.15,.11);}}else if(kind==='signal-short'){{tone(520,0,.11);tone(420,.12,.14);}}else{{tone(620,0,.11);tone(820,.12,.16);}}}}
  function dismiss(el){{if(!el)return;el.style.animation='alertOut .18s ease-in both';setTimeout(()=>el.remove(),190);}}
  function toast(e,index=0){{const kind=eventKind(e),text=eventText(e),el=document.createElement('div');el.className=`alert-toast ${{kind}}`;el.dataset.focusSymbol=e.symbol;el.innerHTML=`<div class="alert-icon">${{text.icon}}</div><div class="alert-main"><strong>${{esc(text.title)}}</strong><small>${{esc(text.detail)}} · İncelemek için tıkla</small></div><button class="alert-close" type="button" aria-label="Kapat">×</button>`;stack.prepend(el);el.querySelector('.alert-close').addEventListener('click',ev=>{{ev.stopPropagation();dismiss(el);}});el.addEventListener('click',()=>dismiss(el));setTimeout(()=>dismiss(el),7000+index*450);setTimeout(()=>play(kind),index*170);}}
  function ingest(data){{const events=buildEvents(data),seen=seenSet();let initialized=false;try{{initialized=localStorage.getItem(INIT)==='1';}}catch{{}}if(!initialized){{events.forEach(e=>seen.add(e.id));saveSeen(seen);try{{localStorage.setItem(INIT,'1');}}catch{{}}return;}}const fresh=events.filter(e=>!seen.has(e.id));fresh.forEach(e=>seen.add(e.id));saveSeen(seen);fresh.slice(0,3).reverse().forEach((e,i)=>toast(e,i));if(fresh.length>3){{const e={{type:'result',symbol:'SİSTEM',outcome:`+${{fresh.length-3}} YENİ`,system:'Diğer bildirimler Bildirim Merkezi’nde',dir:'',ts:0}};toast(e,3);}}}}
  toggle.addEventListener('click',()=>{{const on=!isSoundOn();setSound(on);if(on){{ensureAudio();play('signal-long');}}}});
  document.addEventListener('pointerdown',()=>{{if(isSoundOn()&&!armed)ensureAudio();}},{{once:true,capture:true}});
  window.addEventListener('kripto-dashboard-data',event=>ingest(event.detail||{{}}));
  if(window.__kriptoDashboardData)ingest(window.__kriptoDashboardData);
  setSound(isSoundOn());
}})();
</script>
'''
    return body.replace("</body>", stack + "\n" + script + "\n</body>", 1)


def make_v24_handler(config: PanelConfig, service, sessions, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = notify.make_v23_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V24Handler(BaseHandler):
        server_version = "KriptoPanel/2.4"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, alert_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            return super().do_GET()

    return V24Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.4 sesli renkli uyarı arayüzü.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = notify.home.v21.focus.v2.v19.v18.v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = notify.home.v21.focus.v2.v19.v18.account_store_from_env(config)
    handler = make_v24_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        OKXMarketDataClient(),
        notify.home.v21.market.OKXMarketOverviewClient(),
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} sound_alert=optional colored_toast=on notifications=on")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
