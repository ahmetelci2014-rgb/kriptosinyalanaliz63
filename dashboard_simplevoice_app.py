"""Kripto Kontrol Merkezi V3.33 - sade canlı durum ve sesli bildirim UX.

V3.32 ve önceki bütün panel katmanlarını korur. Bu katman yalnız ana ürün
panelinin anlaşılabilirliğini artırır:
- Tek satırda CANLI / veri bekleniyor durumunu gösterir.
- Kullanıcı açıkça açarsa yeni sinyal ve TP/SL/BE olaylarını kısa ton + Türkçe
  tarayıcı sesiyle okur; ilk açılışta mevcut kayıtları seslendirmez.
- Eski V2.4 ses altyapısını silmeden tek, anlaşılır sesli bildirim kontrolünde
  birleştirir.
- Mobil alt menüyü dört ana hedefe sadeleştirir; Piyasa rotası silinmez ve ana
  sayfa / coin akışlarından erişilebilir kalır.
- Sinyal kartlarını mobilde daha okunur hale getirir; teknik ayrıntı eklemez.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger,
üyelik/ödeme backend'i ve otomatik filtre davranışı değiştirilmez.
"""
from __future__ import annotations

import argparse
import html
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_marketcoinux_app as marketcoin
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_33_SIMPLE_VOICE_2026_08_16"

CSS = r'''
/* V3.33 - AtikAnaliz'deki anlaşılır mobil yaklaşımından esinlenen özgün sade katman */
#soundToggle{display:none!important}
.v333-status{min-height:48px;border-bottom:1px solid var(--line);background:#08131b;display:flex;align-items:center;gap:12px;padding:7px 26px;color:#8ea8a4}
.v333-live{display:flex;align-items:center;gap:9px;min-width:0;flex:1}.v333-live-dot{width:9px;height:9px;border-radius:50%;background:#607a77;flex:0 0 auto}.v333-live-copy{min-width:0}.v333-live-copy b{display:block;font-size:10px;letter-spacing:.05em}.v333-live-copy small{display:block;color:#607c78;font-size:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v333-status.live .v333-live-dot{background:var(--green);box-shadow:0 0 9px rgba(66,226,140,.7)}.v333-status.live .v333-live-copy b{color:var(--green)}
.v333-status.warn .v333-live-dot{background:var(--amber);box-shadow:0 0 8px rgba(255,189,89,.45)}.v333-status.warn .v333-live-copy b{color:var(--amber)}
.v333-status.danger .v333-live-dot{background:var(--red);box-shadow:0 0 8px rgba(255,98,125,.5)}.v333-status.danger .v333-live-copy b{color:var(--red)}
.v333-voice{border:1px solid #29444c;background:#0a1922;color:#9db5b1;border-radius:999px;padding:7px 10px;font-size:9px;font-weight:900;white-space:nowrap;min-height:34px}.v333-voice.on{border-color:rgba(44,230,191,.38);background:rgba(44,230,191,.08);color:var(--teal)}.v333-voice.unsupported{opacity:.55;cursor:not-allowed}
.v333-last{display:none;color:#6f8c88;font-size:8px;white-space:nowrap;max-width:260px;overflow:hidden;text-overflow:ellipsis}
#page-signals .row-card.v333-long{border-left:3px solid rgba(66,226,140,.72)}#page-signals .row-card.v333-short{border-left:3px solid rgba(255,98,125,.72)}
#page-signals .row-card .tag.long,#page-signals .row-card .tag.short{font-weight:950}
@media(max-width:760px){
 .v333-status{padding:6px 11px;min-height:50px;gap:7px}.v333-live{gap:7px}.v333-live-copy b{font-size:9px}.v333-live-copy small{font-size:7.5px}.v333-voice{min-height:36px;padding:6px 9px;font-size:8px}
 .mobile-nav a[href="/market-center"]{display:none!important}.mobile-nav button,.mobile-nav a{flex:1 1 25%!important}
 #page-signals .row-card{padding:11px!important;gap:7px!important}#page-signals .row-card .coin strong{font-size:13px!important}#page-signals .row-card .coin small{font-size:9px!important}#page-signals .row-card .tag{font-size:9px!important;padding:5px 8px!important}
 #page-signals .v328-card-toggle,#page-signals .v329-coin-link{min-height:36px!important}
 #page-home .home-strong{padding:12px!important}.page-head{margin-bottom:14px!important}
}
@media(min-width:1100px){.v333-last{display:block}}
@media(prefers-reduced-motion:reduce){.v333-live-dot{box-shadow:none!important}}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v333-simplevoice-script">
(()=>{'use strict';if(window.__v333SimpleVoice)return;window.__v333SimpleVoice=true;
const VOICE='kripto_voice_notify_v333',SEEN='kripto_voice_seen_v333',INIT='kripto_voice_initialized_v333',OLD_SOUND='kripto_alert_sound_v24';
let audio=null,lastDataAt=0,lastQualityOk=true,lastAlert='';
const $=id=>document.getElementById(id);
const normalize=v=>String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
const direction=r=>String(r?.direction||'').toUpperCase();
const outcome=r=>String(r?.outcome||r?.result||'').toUpperCase();
const system=r=>String(r?.system_label||r?.system||r?.source||'Sistem');
const parseTs=v=>{if(v===null||v===undefined||v==='')return 0;if(typeof v==='number'||/^\d+(\.\d+)?$/.test(String(v))){let n=Number(v);if(!Number.isFinite(n))return 0;if(n>1e12)n/=1000;return Math.round(n)}const d=new Date(v);return Number.isNaN(d.getTime())?0:Math.round(d.getTime()/1000)};
const signalTs=r=>parseTs(r?.opened_at||r?.sent_at||r?.created_at||r?.detected_at||r?.updated_at);
const resultTs=r=>parseTs(r?.closed_at||r?.finalized_at||r?.updated_at||r?.opened_at||r?.sent_at);
function voiceOn(){try{return localStorage.getItem(VOICE)==='1'}catch{return false}}
function saveVoice(on){try{localStorage.setItem(VOICE,on?'1':'0');localStorage.setItem(OLD_SOUND,'0')}catch{}}
function seenSet(){try{const raw=JSON.parse(localStorage.getItem(SEEN)||'[]');return new Set(Array.isArray(raw)?raw:[])}catch{return new Set()}}
function saveSeen(set){try{localStorage.setItem(SEEN,JSON.stringify([...set].slice(-900)))}catch{}}
function key(type,row,ts){return [type,normalize(row?.symbol),system(row),direction(row)||outcome(row),ts].join('|')}
function buildEvents(data){const rows=[];const open=Array.isArray(data?.open_trades)?data.open_trades:[],results=Array.isArray(data?.recent_results)?data.recent_results:[];open.forEach(r=>{const symbol=normalize(r.symbol),ts=signalTs(r);if(symbol)rows.push({id:key('signal',r,ts),type:'signal',symbol,dir:direction(r),entry:r.entry,ts})});results.forEach(r=>{const symbol=normalize(r.symbol),ts=resultTs(r),o=outcome(r)||'KAPALI';if(symbol)rows.push({id:key('result',r,ts),type:'result',symbol,dir:direction(r),outcome:o,ts})});return rows.sort((a,b)=>b.ts-a.ts).slice(0,120)}
function coinVoice(symbol){const clean=normalize(symbol),base=clean.endsWith('USDT')?clean.slice(0,-4):clean;return `${base.split('').join(' ')} U S D T`}
function priceVoice(value){const n=Number(value);if(!Number.isFinite(n))return'';return n.toLocaleString('tr-TR',{maximumFractionDigits:8})}
function eventKind(e){if(e.type==='signal')return e.dir==='SHORT'?'short':'long';const o=String(e.outcome||'').toUpperCase();if(o.startsWith('TP'))return'tp';if(o==='SL'||o.startsWith('SL_'))return'sl';if(o.includes('BE'))return'be';return'info'}
function voiceText(e){const coin=coinVoice(e.symbol);if(e.type==='signal'){const dir=e.dir==='SHORT'?'şort':'long';const entry=priceVoice(e.entry);return `Yeni ${dir} sinyali. ${coin}.${entry?` Giriş ${entry}.`:''}`};const o=String(e.outcome||'').toUpperCase();if(o.startsWith('TP'))return `${coin}. ${o.replace('TP','T P ')} geldi.`;if(o==='SL'||o.startsWith('SL_'))return `${coin}. Stop oldu.`;if(o.includes('BE'))return `${coin}. Başa baş kapandı.`;return `${coin}. Yeni sonuç ${o||'kapalı'}.`}
function ensureAudio(){const C=window.AudioContext||window.webkitAudioContext;if(!C)return null;try{audio=audio||new C();if(audio.state==='suspended')audio.resume();return audio}catch{return null}}
function tone(freq,start,duration,gain=.045){const ctx=ensureAudio();if(!ctx||ctx.state!=='running')return;const o=ctx.createOscillator(),g=ctx.createGain();o.type='sine';o.frequency.value=freq;g.gain.setValueAtTime(.0001,ctx.currentTime+start);g.gain.exponentialRampToValueAtTime(gain,ctx.currentTime+start+.015);g.gain.exponentialRampToValueAtTime(.0001,ctx.currentTime+start+duration);o.connect(g);g.connect(ctx.destination);o.start(ctx.currentTime+start);o.stop(ctx.currentTime+start+duration+.02)}
function ping(kind){if(kind==='tp'){tone(720,0,.11);tone(930,.12,.14)}else if(kind==='sl'){tone(320,0,.15);tone(220,.16,.19)}else if(kind==='be'){tone(520,0,.10);tone(520,.13,.10)}else if(kind==='short'){tone(520,0,.10);tone(410,.11,.13)}else{tone(620,0,.10);tone(820,.11,.14)}}
function speak(text){if(!voiceOn()||!('speechSynthesis'in window)||!('SpeechSynthesisUtterance'in window))return;try{window.speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);u.lang='tr-TR';u.rate=.98;u.pitch=1;u.volume=.92;const voices=window.speechSynthesis.getVoices?.()||[];const tr=voices.find(v=>String(v.lang||'').toLowerCase().startsWith('tr'));if(tr)u.voice=tr;window.speechSynthesis.speak(u)}catch{}}
function announce(e){if(!voiceOn())return;ping(eventKind(e));setTimeout(()=>speak(voiceText(e)),150);lastAlert=e.type==='signal'?`${e.symbol} ${e.dir||'SİNYAL'}`:`${e.symbol} ${e.outcome||'SONUÇ'}`;renderStatus()}
function renderVoiceButton(){const b=$('v333VoiceToggle');if(!b)return;const supported=('speechSynthesis'in window)||!!(window.AudioContext||window.webkitAudioContext);b.classList.toggle('unsupported',!supported);b.disabled=!supported;const on=voiceOn();b.classList.toggle('on',on);b.textContent=on?'🔊 Sesli bildirim açık':'🔇 Sesli bildirim kapalı';b.setAttribute('aria-pressed',on?'true':'false');b.title=on?'Yeni sinyal ve sonuçları sesli bildirir':'Sesli bildirimleri aç'}
function renderStatus(){const strip=$('v333Status'),title=$('v333LiveTitle'),sub=$('v333LiveSub'),last=$('v333Last');if(!strip||!title||!sub)return;strip.classList.remove('live','warn','danger');if(!lastDataAt){strip.classList.add('warn');title.textContent='BAĞLANTI BEKLENİYOR';sub.textContent='Panel verisi bekleniyor';return}const age=Math.max(0,Math.round((Date.now()-lastDataAt)/1000));if(lastQualityOk===false){strip.classList.add('warn');title.textContent='SON GEÇERLİ VERİ';sub.textContent=`Kaynak kontrolü gerekli · ${age} sn önce`;}
else if(age<=75){strip.classList.add('live');title.textContent='CANLI';sub.textContent=`Veri akışı ${age<5?'şimdi':age+' sn önce'} yenilendi`;}
else if(age<=150){strip.classList.add('warn');title.textContent='VERİ BEKLENİYOR';sub.textContent=`Son panel verisi ${age} sn önce`;}
else{strip.classList.add('danger');title.textContent='BAĞLANTIYI KONTROL ET';sub.textContent=`Son panel verisi ${Math.round(age/60)} dk önce`;}
if(last)last.textContent=lastAlert?`Son uyarı · ${lastAlert}`:''}
function makeStrip(){const content=document.querySelector('.content'),top=document.querySelector('.topbar');if(!content||!top||$('v333Status'))return;const strip=document.createElement('div');strip.id='v333Status';strip.className='v333-status warn';strip.innerHTML='<div class="v333-live"><span class="v333-live-dot"></span><div class="v333-live-copy"><b id="v333LiveTitle">BAĞLANTI BEKLENİYOR</b><small id="v333LiveSub">Panel verisi bekleniyor</small></div></div><span class="v333-last" id="v333Last"></span><button class="v333-voice" id="v333VoiceToggle" type="button" aria-pressed="false">🔇 Sesli bildirim kapalı</button>';top.insertAdjacentElement('afterend',strip);$('v333VoiceToggle')?.addEventListener('click',()=>{const on=!voiceOn();saveVoice(on);renderVoiceButton();if(on){ensureAudio();ping('long');setTimeout(()=>speak('Sesli bildirimler açık.'),140)}else{try{window.speechSynthesis?.cancel()}catch{}}});renderVoiceButton();renderStatus()}
function decorateSignalCards(){const root=$('signalsList');if(!root)return;const apply=()=>root.querySelectorAll('.row-card').forEach(card=>{card.classList.toggle('v333-long',!!card.querySelector('.tag.long'));card.classList.toggle('v333-short',!!card.querySelector('.tag.short'))});apply();new MutationObserver(apply).observe(root,{childList:true,subtree:true})}
function ingest(data){lastDataAt=Date.now();lastQualityOk=data?.data_quality?.ok!==false;renderStatus();const events=buildEvents(data),seen=seenSet();let initialized=false;try{initialized=localStorage.getItem(INIT)==='1'}catch{}if(!initialized){events.forEach(e=>seen.add(e.id));saveSeen(seen);try{localStorage.setItem(INIT,'1')}catch{}return}const fresh=events.filter(e=>!seen.has(e.id));fresh.forEach(e=>seen.add(e.id));saveSeen(seen);if(fresh.length)announce(fresh[0])}
function migrateSound(){try{if(localStorage.getItem(VOICE)===null&&localStorage.getItem(OLD_SOUND)==='1')localStorage.setItem(VOICE,'1');localStorage.setItem(OLD_SOUND,'0')}catch{}}
function init(){if(location.pathname!=='/')return;migrateSound();makeStrip();decorateSignalCards();window.addEventListener('kripto-dashboard-data',e=>ingest(e.detail||{}));if(window.__kriptoDashboardData)ingest(window.__kriptoDashboardData);setInterval(renderStatus,10000)}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
</script>
'''


def enhance_simple_voice_ui(body: str, nonce: str) -> str:
    """Ana panelde sade canlı durum + kullanıcı kontrollü Türkçe ses ekler."""
    if 'id="v333-simplevoice-script"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v333_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store,
    market_client=None,
    overview_client=None,
    history_cache: earlyperf.HistoricalPulseCache | None = None,
):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = marketcoin.make_v332_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V333Handler(BaseHandler):
        server_version = "KriptoPanel/3.33"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/" and self._session():
                    body = enhance_simple_voice_ui(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "simple_live_status": True,
                    "voice_notifications": "user_opt_in",
                    "voice_language": "tr-TR",
                    "voice_events": ["signal", "TP", "SL", "BE"],
                    "existing_events_spoken_on_first_load": False,
                    "mobile_primary_nav_max": 4,
                    "market_route": "preserved",
                    "market_coin_ux": "preserved",
                    "role_boundary": "preserved",
                    "account_ux": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V333Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.33 Sade Sesli Bildirim UX")
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
    candle_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v333_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} simple_voice=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
