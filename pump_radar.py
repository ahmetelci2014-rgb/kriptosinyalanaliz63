# pump_radar.py
# Erken Pump / Hacim Patlama Radarı v1
# Emir açmaz. Sadece Telegram uyarısı gönderir.

import os, json, time, math, requests, ccxt
from datetime import datetime, timezone, timedelta

TOKEN=os.getenv('TOKEN')
CHAT_ID=os.getenv('CHAT_ID')
BOT_NAME='Erken Pump Radarı v1'
STATE_FILE='pump_radar_state.json'
TR_TZ=timezone(timedelta(hours=3))

MAX_COINS_PER_SOURCE=120
MAX_ALERTS_PER_RUN=6
MIN_24H_QUOTE_VOLUME=80_000
DUPLICATE_SECONDS=45*60

MIN_WATCH_5M_CHANGE=0.45
MIN_WATCH_5M_VOLUME_RATIO=1.80
MIN_5M_CHANGE=0.80
MIN_15M_CHANGE=1.50
MIN_5M_VOLUME_RATIO=2.50
MIN_15M_VOLUME_RATIO=2.00
LATE_1H_CHANGE_PERCENT=25.0
LATE_15M_CHANGE_PERCENT=12.0

def send_telegram(msg):
    if not TOKEN or not CHAT_ID:
        print('TOKEN/CHAT_ID yok')
        return False
    try:
        r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',data={'chat_id':CHAT_ID,'text':msg},timeout=20)
        print('Telegram:',r.status_code,r.text)
        return r.status_code==200
    except Exception as e:
        print('Telegram hata:',e); return False

def load_state():
    try:
        if not os.path.exists(STATE_FILE): return {}
        txt=open(STATE_FILE,'r',encoding='utf-8').read().strip()
        return json.loads(txt) if txt else {}
    except Exception as e:
        print('state okuma hata:',e); return {}

def save_state(state):
    try:
        open(STATE_FILE,'w',encoding='utf-8').write(json.dumps(state,indent=2,ensure_ascii=False))
        return True
    except Exception as e:
        print('state kayıt hata:',e); return False

def now_ts(): return int(time.time())

def fnum(v):
    try:
        v=float(v)
        if math.isnan(v) or math.isinf(v): return 0.0
        return v
    except Exception:
        return 0.0

def fmt(v):
    v=fnum(v)
    if v>=100: return f'{v:.2f}'
    if v>=1: return f'{v:.4f}'
    if v>=0.01: return f'{v:.6f}'
    return f'{v:.10f}'

def pct(new,old):
    old=fnum(old); new=fnum(new)
    return ((new-old)/old)*100 if old else 0.0

def make_exchange(exchange_id,market_type):
    if exchange_id=='okx':
        return ccxt.okx({'enableRateLimit':True,'options':{'defaultType':market_type}})
    if exchange_id=='binance':
        return ccxt.binance({'enableRateLimit':True,'options':{'defaultType':market_type}})
    raise RuntimeError('Bilinmeyen borsa')

def sources():
    return [
        {'name':'OKX Spot','exchange_id':'okx','market_type':'spot','spot':True,'swap':False},
        {'name':'OKX Futures','exchange_id':'okx','market_type':'swap','spot':False,'swap':True},
        {'name':'Binance Spot','exchange_id':'binance','market_type':'spot','spot':True,'swap':False},
        {'name':'Binance Futures','exchange_id':'binance','market_type':'future','spot':False,'swap':True},
    ]

def quote_vol(ticker):
    v=fnum(ticker.get('quoteVolume'))
    if v>0: return v
    info=ticker.get('info',{}) or {}
    for k in ['volCcy24h','volUsd24h','quoteVolume','turnover24h']:
        v=fnum(info.get(k))
        if v>0: return v
    return 0.0

def percent24(ticker):
    if ticker.get('percentage') is not None: return fnum(ticker.get('percentage'))
    info=ticker.get('info',{}) or {}
    for k in ['changeRate','priceChangePercent','sodUtc8']:
        if info.get(k) is not None:
            v=fnum(info.get(k))
            return v*100 if abs(v)<=1 else v
    return 0.0

def list_markets(ex,src):
    mk=ex.load_markets(); out=[]; stable={'USDT','USDC','FDUSD','TUSD','DAI','BUSD','USDP','USD'}
    for sym,m in mk.items():
        try:
            if not m.get('active',True): continue
            if str(m.get('quote','')).upper()!='USDT': continue
            base=str(m.get('base','')).upper()
            if not base or base in stable: continue
            if src['spot'] and not m.get('spot',False): continue
            if src['swap']:
                if not m.get('swap',False): continue
                if str(m.get('settle','USDT') or 'USDT').upper()!='USDT': continue
            out.append(sym)
        except Exception: pass
    return out

def select_markets(ex,src):
    syms=list_markets(ex,src)
    if not syms: return []
    try: tickers=ex.fetch_tickers(syms)
    except Exception as e:
        print(src['name'],'fetch_tickers hata:',e); tickers={}
    rows=[]
    for s in syms:
        t=tickers.get(s,{})
        qv=quote_vol(t); p=percent24(t)
        if qv>=MIN_24H_QUOTE_VOLUME:
            rows.append({'s':s,'qv':qv,'p':p})
    if not rows: return syms[:MAX_COINS_PER_SOURCE]
    gain=sorted(rows,key=lambda x:x['p'],reverse=True)[:MAX_COINS_PER_SOURCE//2]
    vol=sorted(rows,key=lambda x:x['qv'],reverse=True)[:MAX_COINS_PER_SOURCE//2]
    chosen=[]; seen=set()
    for r in gain+vol:
        if r['s'] in seen: continue
        seen.add(r['s']); chosen.append(r['s'])
    return chosen[:MAX_COINS_PER_SOURCE]

def fetch5(ex,sym):
    try:
        o=ex.fetch_ohlcv(sym,timeframe='5m',limit=120)
        return o if o and len(o)>=35 else None
    except Exception as e:
        print(sym,'5m hata:',e); return None

def analyze(src_name,sym,o):
    last=o[-2]
    close=fnum(last[4]); open_=fnum(last[1]); high=fnum(last[2]); low=fnum(last[3]); vol=fnum(last[5])
    if close<=0 or open_<=0: return None
    ch5=pct(close,open_)
    ch15=pct(close,o[-5][4] if len(o)>=5 else o[-3][4])
    ch1h=pct(close,o[-14][4] if len(o)>=14 else o[-3][4])
    ch2h=pct(close,o[-26][4] if len(o)>=26 else o[-3][4])
    vols=[fnum(x[5]) for x in o[-32:-2]]
    avg=sum(vols)/len(vols) if vols else 0
    vr5=vol/avg if avg>0 else 0
    vol15=sum(fnum(x[5]) for x in o[-4:-1])
    prev15=[]
    for i in range(7,34,3):
        chunk=o[-i:-i+3]
        if len(chunk)==3: prev15.append(sum(fnum(x[5]) for x in chunk))
    avg15=sum(prev15)/len(prev15) if prev15 else 0
    vr15=vol15/avg15 if avg15>0 else 0
    prev_high=max(fnum(x[2]) for x in o[-42:-2])
    breakout=close>prev_high
    green=0
    for c in reversed(o[-7:-1]):
        if fnum(c[4])>fnum(c[1]): green+=1
        else: break
    support=min(fnum(x[3]) for x in o[-16:-2])
    resistance=max(fnum(x[2]) for x in o[-30:-2])
    score=0; reasons=[]
    if ch5>=MIN_5M_CHANGE: score+=22; reasons.append(f'5M güçlü yeşil %{round(ch5,2)}')
    elif ch5>=MIN_WATCH_5M_CHANGE: score+=12; reasons.append(f'5M erken hareket %{round(ch5,2)}')
    if ch15>=MIN_15M_CHANGE: score+=22; reasons.append(f'15M yükseliş %{round(ch15,2)}')
    if vr5>=MIN_5M_VOLUME_RATIO: score+=24; reasons.append(f'5M hacim patlaması {round(vr5,2)}x')
    elif vr5>=MIN_WATCH_5M_VOLUME_RATIO: score+=12; reasons.append(f'5M hacim artıyor {round(vr5,2)}x')
    if vr15>=MIN_15M_VOLUME_RATIO: score+=18; reasons.append(f'15M hacim güçlü {round(vr15,2)}x')
    if breakout: score+=18; reasons.append('kısa vade direnç kırılımı')
    if green>=3: score+=8; reasons.append(f'{green} mum üst üste yeşil')
    late=ch1h>=LATE_1H_CHANGE_PERCENT or ch15>=LATE_15M_CHANGE_PERCENT
    if late:
        score-=15; stage='GEÇ KALMIŞ RİSKLİ PUMP'
    elif score>=78: stage='PUMP BAŞLANGIÇ ALARMI'
    elif score>=58: stage='ERKEN HACİM RADARI'
    else: return None
    risk=abs(pct(close,support)) if support>0 else 0
    return {'source':src_name,'market_symbol':sym,'stage':stage,'score':int(score),'price':close,'change_5m':ch5,'change_15m':ch15,'change_1h':ch1h,'change_2h':ch2h,'vr5':vr5,'vr15':vr15,'breakout':breakout,'green':green,'support':support,'resistance':resistance,'risk':risk,'tp1':close*1.035,'tp2':close*1.07,'tp3':close*1.12,'late':late,'reasons':reasons}

def key(a): return f"{a['source']}::{a['market_symbol']}::{a['stage']}"
def dup(a,state): return now_ts()-int(state.get('last_alerts',{}).get(key(a),0))<DUPLICATE_SECONDS
def mark(a,state):
    state.setdefault('last_alerts',{})[key(a)]=now_ts()
    cutoff=now_ts()-24*3600
    state['last_alerts']={k:v for k,v in state['last_alerts'].items() if int(v)>=cutoff}

def msg(a):
    sym=a['market_symbol'].replace(':USDT','')
    late='\n⚠️ Bu hareket erken aşamayı geçmiş olabilir. Tepeden kovalamak risklidir.' if a['late'] else ''
    risk=f"Yakın destek / iptal bölgesi: {fmt(a['support'])} | Uzaklık: %{round(a['risk'],2)}"
    if a['risk']>4: risk+='\n⚠️ Destek uzak. Kovalamak riskli olabilir.'
    return f"""🚨 {BOT_NAME}

Aşama: {a['stage']}
Coin: {sym}
Kaynak: {a['source']}
Skor: %{a['score']}

💰 Fiyat: {fmt(a['price'])}

📈 Hareket:
• 5M: %{round(a['change_5m'],2)}
• 15M: %{round(a['change_15m'],2)}
• 1H: %{round(a['change_1h'],2)}
• 2H: %{round(a['change_2h'],2)}

📊 Hacim:
• 5M hacim oranı: {round(a['vr5'],2)}x
• 15M hacim oranı: {round(a['vr15'],2)}x

📌 Teknik:
• Direnç kırılımı: {'Evet' if a['breakout'] else 'Hayır'}
• Üst üste yeşil mum: {a['green']}
• Yakın direnç: {fmt(a['resistance'])}
• {risk}

🎯 Sadece takip senaryosu:
• TP1: {fmt(a['tp1'])}
• TP2: {fmt(a['tp2'])}
• TP3: {fmt(a['tp3'])}

🧠 Neden geldi:
{', '.join(a['reasons'])}
{late}

📌 Kural:
Bu otomatik işlem emri değildir.
Fiyat çok yükseldiyse kovalanmaz.
Küçük risk, düşük kaldıraç ve stop şarttır.""".strip()

def scan_source(src,state):
    alerts=[]
    try:
        ex=make_exchange(src['exchange_id'],src['market_type'])
        markets=select_markets(ex,src)
        print(src['name'],'taranacak:',len(markets))
        for s in markets:
            try:
                o=fetch5(ex,s)
                if not o: continue
                a=analyze(src['name'],s,o)
                if not a: continue
                if dup(a,state): continue
                alerts.append(a)
                time.sleep(0.05)
            except Exception as e:
                print(src['name'],s,'analiz hata:',e)
    except Exception as e:
        print(src['name'],'kaynak hata:',e)
    return alerts

def main():
    print(BOT_NAME,'başladı')
    state=load_state(); alerts=[]
    for src in sources():
        alerts.extend(scan_source(src,state))
    alerts=sorted(alerts,key=lambda x:(x['score'],x['vr5'],x['change_15m']),reverse=True)
    selected=alerts[:MAX_ALERTS_PER_RUN]
    if not selected:
        print('Uygun erken pump yok')
        if now_ts()-int(state.get('last_status',0))>3600:
            send_telegram(f'📡 {BOT_NAME} çalıştı.\n\nUygun erken pump/hacim patlaması bulunamadı.\nSistem taramaya devam ediyor.')
            state['last_status']=now_ts(); save_state(state)
        return
    send_telegram(f'🚨 {BOT_NAME} çalıştı.\n\nBulunan aday: {len(alerts)}\nGönderilen uyarı: {len(selected)}\n\nBu uyarılar erken hacim/pump radarıdır. İşlem emri değildir.')
    for a in selected:
        if send_telegram(msg(a)): mark(a,state)
        time.sleep(1)
    state['last_run']=now_ts(); state['last_run_text']=datetime.now(TR_TZ).strftime('%Y-%m-%d %H:%M:%S')
    save_state(state)
    print(BOT_NAME,'tamamlandı')

if __name__=='__main__': main()
