"""Premium post-trade SL/BE diagnosis.

Shadow-only. Reads/writes trade_ledger.json, sends no Telegram messages, opens no
orders and never changes live Entry/TP/SL/BE rules.
"""
from __future__ import annotations
import json, os, tempfile, time
from collections import Counter

VERSION="RESULT_DIAGNOSTICS_V1_2026_08_24"
LEDGER_FILE="trade_ledger.json"
CHECKPOINTS=(15,30,60,120,240)
MAX_MINUTES=240
RESTORE_HOURS=24
MAX_TRADES_PER_RUN=20
FETCH_LIMIT=300
TRACKED={"SL","BE","TP1_SONRASI_BE","TP2_SONRASI_BE"}

def now_ts(): return int(time.time())

def sf(v, default=None):
    try:
        if v in (None,"","-"): return default
        x=float(v)
        return x if x==x else default
    except Exception: return default

def canon(v):
    x=str(v or "").upper().strip()
    if x=="STOP": return "SL"
    if x=="BREAK_EVEN": return "BE"
    return x

def load(path=LEDGER_FILE):
    try:
        with open(path,"r",encoding="utf-8") as f: d=json.load(f)
        if isinstance(d,dict):
            d.setdefault("trades",{})
            return d
    except Exception: pass
    return {"trades":{},"last_update":0}

def save(data,path=LEDGER_FILE):
    folder=os.path.dirname(os.path.abspath(path)) or "."
    tmp=None
    try:
        os.makedirs(folder,exist_ok=True)
        data["last_update"]=now_ts()
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=folder,delete=False,prefix=".result_diag.",suffix=".tmp") as f:
            tmp=f.name
            json.dump(data,f,ensure_ascii=False,indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
        with open(tmp,"r",encoding="utf-8") as f: json.load(f)
        os.replace(tmp,os.path.abspath(path)); tmp=None
        return True
    except Exception as e:
        print("Result diagnostics save error:",e); return False
    finally:
        if tmp and os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass

def okx_symbol(symbol):
    s=str(symbol or "").upper().replace("/","").replace(":","").replace("-","")
    if s.endswith("USDTUSDT"): s=s[:-4]
    if s.endswith("USDT"): s=s[:-4]
    return f"{s}/USDT:USDT"

def exchange():
    import ccxt
    return ccxt.okx({"enableRateLimit":True,"options":{"defaultType":"swap"}})

def candles(ex,symbol,since):
    try:
        rows=ex.fetch_ohlcv(okx_symbol(symbol),timeframe="1m",since=int(since)*1000,limit=FETCH_LIMIT)
        return [{"t":int(r[0]/1000),"h":float(r[2]),"l":float(r[3]),"c":float(r[4])} for r in (rows or [])]
    except Exception as e:
        print(symbol,"result diagnostics candle error:",e); return []

def risk_abs(tr):
    e,s=sf(tr.get("entry")),sf(tr.get("sl"))
    return abs(e-s) if e is not None and s is not None else None

def risk_pct(tr):
    e=sf(tr.get("entry")); r=risk_abs(tr)
    return round(r/e*100,4) if e and r is not None else None

def dir_r(direction,reference,price,risk):
    if None in (reference,price,risk) or risk<=0:return None
    return (price-reference)/risk if direction=="LONG" else (reference-price)/risk

def watched(tr,result):
    names={"SL":("TP1","TP2","TP3"),"BE":("TP1","TP2","TP3"),"TP1_SONRASI_BE":("TP2","TP3"),"TP2_SONRASI_BE":("TP3",)}.get(result,())
    out={}
    for name in names:
        x=sf(tr.get(name.lower()))
        if x is not None: out[name]=x
    return out

def reference(tr,result):
    if result=="SL":
        return sf(tr.get("exit_price"),sf(tr.get("sl"))),"SL_EXIT"
    return sf(tr.get("exit_price"),sf(tr.get("entry"))),"BE_EXIT"

def new_diag(tr,result,now):
    closed=int(tr.get("closed_at") or 0)
    if closed<=0 or now-closed>RESTORE_HOURS*3600:return None
    ref,label=reference(tr,result)
    if ref is None or ref<=0:return None
    return {"version":VERSION,"shadow_only":True,"status":"TRACKING","final_result":result,
            "started_at":closed,"measurement_start_at":((closed//60)+1)*60,
            "reference_price":ref,"reference_label":label,"watched_levels":watched(tr,result),
            "reached_levels":{},"checkpoints":{},"max_favorable_r":0.0,"max_adverse_r":0.0,
            "last_checked_at":0,"completed_at":0,"diagnosis":{"status":"PROVISIONAL","code":"VERI_BIRIKIYOR",
            "likely_cause":"HENUZ_YOK","confidence":"LOW","summary":"Kapanis sonrasi veri birikiyor.","evidence":{}}}

def hit(direction,h,l,level):
    return h>=level if direction=="LONG" else l<=level

def update_path(tr,d,rows,now):
    if not rows:return False
    direction=str(tr.get("direction") or "").upper()
    if direction not in {"LONG","SHORT"}:return False
    ref=sf(d.get("reference_price")); risk=risk_abs(tr)
    if ref is None or not risk:return False
    best=max([ref]+[x["h"] for x in rows]) if direction=="LONG" else min([ref]+[x["l"] for x in rows])
    worst=min([ref]+[x["l"] for x in rows]) if direction=="LONG" else max([ref]+[x["h"] for x in rows])
    d["max_favorable_r"]=round(max(0.0,dir_r(direction,ref,best,risk) or 0.0),4)
    d["max_adverse_r"]=round(max(0.0,-(dir_r(direction,ref,worst,risk) or 0.0)),4)
    reached=d.setdefault("reached_levels",{})
    for name,level in (d.get("watched_levels") or {}).items():
        if name in reached:continue
        for row in rows:
            if hit(direction,row["h"],row["l"],level):
                reached[name]={"first_reached_at":row["t"],"minutes_after_close":max(0,int((row["t"]-d["started_at"])/60)),"level_price":level}
                break
    age=max(0,int((now-d["started_at"])/60)); cps=d.setdefault("checkpoints",{})
    for minute in CHECKPOINTS:
        key=str(minute)
        if key in cps or age<minute:continue
        target=d["started_at"]+minute*60
        eligible=[x for x in rows if x["t"]+60<=target]
        if not eligible:continue
        row=eligible[-1]
        cps[key]={"target_at":target,"candle_time":row["t"],"close_price":round(row["c"],12),
                  "directional_r_from_reference":round(dir_r(direction,ref,row["c"],risk) or 0.0,4)}
    d["last_checked_at"]=now; d["last_price"]=round(rows[-1]["c"],12)
    done=age>=MAX_MINUTES and str(MAX_MINUTES) in cps
    d["status"]="COMPLETED" if done else "TRACKING"
    if done:d["completed_at"]=now
    return True

def reached(d,name): return name in (d.get("reached_levels") or {})

def mins(d,name):
    try:return int((d.get("reached_levels") or {}).get(name,{}).get("minutes_after_close"))
    except Exception:return None

def diagnose(tr,d,final=False):
    result=canon(d.get("final_result")); mfe=max(0.0,sf(d.get("max_favorable_r"),0.0)); mae=max(0.0,sf(d.get("max_adverse_r"),0.0))
    rp=risk_pct(tr); prog=sf(tr.get("tp1_progress_at_send_percent")); dist=sf(tr.get("entry_distance_at_send_percent"))
    ev={"max_favorable_r_after_close":round(mfe,4),"max_adverse_r_after_close":round(mae,4),"risk_percent":rp,
        "tp1_progress_at_send_percent":prog,"entry_distance_at_send_percent":dist,
        "reached_tp1_after_close":reached(d,"TP1"),"reached_tp2_after_close":reached(d,"TP2"),"reached_tp3_after_close":reached(d,"TP3"),
        "tp1_minutes_after_close":mins(d,"TP1"),"tp2_minutes_after_close":mins(d,"TP2"),"tp3_minutes_after_close":mins(d,"TP3")}
    status="FINAL" if final else "PROVISIONAL"
    def out(code,cause,conf,summary):return {"status":status,"code":code,"likely_cause":cause,"confidence":conf,"summary":summary,"evidence":ev}
    if result=="SL":
        if reached(d,"TP2") or reached(d,"TP3"):
            cause="DAR_STOP_OLASILIGI" if rp is not None and rp<=0.70 else ("ERKEN_GIRIS_OLASILIGI" if prog is not None and prog<10 else "GIRIS_STOP_ZAMANLAMASI")
            return out("SL_SONRASI_GUCLU_TOPARLANMA",cause,"HIGH","Stop sonrasi fiyat TP2/TP3 yonune geri dondu; yon tamamen yanlis olmayabilir.")
        if reached(d,"TP1") or mfe>=0.55:
            cause="DAR_STOP_OLASILIGI" if rp is not None and rp<=0.70 else ("ERKEN_GIRIS_OLASILIGI" if prog is not None and prog<10 else "GIRIS_STOP_ZAMANLAMASI")
            return out("SL_SONRASI_TOPARLANMA",cause,"MEDIUM","Stop sonrasi anlamli toparlanma var; giris/stop zamanlamasi incelenmeli.")
        if mae>=0.75 and mfe<0.25:
            return out("SL_SONRASI_TERS_YON_DEVAMI","YON_VEYA_SETUP_GECERSIZLESMESI","HIGH","Stop sonrasi ters hareket devam etti; yon/setup hatasi olasiligi yuksek.")
        return out("SL_KARISIK_SONUC","KESIN_NEDEN_YOK","LOW","Stop sonrasi fiyat yolu tek bir nedene isaret etmiyor.")
    if result in {"BE","TP1_SONRASI_BE","TP2_SONRASI_BE"}:
        if reached(d,"TP3"):
            return out("BE_SONRASI_TP3","BE_KORUMASI_ERKEN_OLABILIR","HIGH","BE kapanisindan sonra fiyat TP3'e ulasti; koruma zamani erken olabilir.")
        if reached(d,"TP2") or (result=="BE" and reached(d,"TP1")):
            return out("BE_SONRASI_YENIDEN_HEDEF","BE_KORUMASI_ERKEN_OLABILIR","MEDIUM","BE kapanisindan sonra fiyat yeniden hedef bolgesine dondu.")
        if mae>=0.50 and mfe<0.35:
            return out("BE_KORUMASI_DOGRU","SERMAYE_KORUMASI_ISABETLI","HIGH","BE sonrasinda fiyat belirgin terslesti; koruma fayda saglamis gorunuyor.")
        return out("BE_NOTR_KARISIK","BE_AYARI_ICIN_DAHA_COK_VERI","LOW","BE icin net erken/koruyucu sonuc yok; daha fazla ornek gerekli.")
    return out("DESTEKLENMEYEN_SONUC","YOK","LOW","Tani uretilmedi.")

def summary(data):
    c=Counter(); causes=Counter(); total=done=0
    for tr in (data.get("trades") or {}).values():
        if not isinstance(tr,dict):continue
        d=tr.get("result_diagnostics")
        if not isinstance(d,dict):continue
        dx=d.get("diagnosis") or {}; c[str(dx.get("code") or "UNKNOWN")]+=1; causes[str(dx.get("likely_cause") or "UNKNOWN")]+=1
        total+=1; done+=str(d.get("status") or "").upper()=="COMPLETED"
    core={"version":VERSION,"tracked_total":total,"completed_total":done,"diagnosis_counts":dict(c),"likely_cause_counts":dict(causes),
          "sl_recovery_total":c["SL_SONRASI_GUCLU_TOPARLANMA"]+c["SL_SONRASI_TOPARLANMA"],
          "be_maybe_early_total":c["BE_SONRASI_TP3"]+c["BE_SONRASI_YENIDEN_HEDEF"],
          "be_protection_correct_total":c["BE_KORUMASI_DOGRU"]}
    old=dict(data.get("result_diagnostics_summary") or {}); old.pop("updated_at",None)
    if old==core:return False
    data["result_diagnostics_summary"]={**core,"updated_at":now_ts()}; return True

def process(ex,tr,now):
    result=canon(tr.get("final_result"))
    if result not in TRACKED:return False,False
    closed=int(tr.get("closed_at") or 0)
    if closed<=0:return False,False
    d=tr.get("result_diagnostics"); changed=False
    if not isinstance(d,dict):
        d=new_diag(tr,result,now)
        if d is None:return False,False
        tr["result_diagnostics"]=d; changed=True
    if str(d.get("status") or "").upper()=="COMPLETED":
        dx=diagnose(tr,d,True)
        if d.get("diagnosis")!=dx:d["diagnosis"]=dx; changed=True
        return changed,False
    start=int(d.get("measurement_start_at") or (((closed//60)+1)*60)); end=min(now,closed+MAX_MINUTES*60)
    rows=[x for x in candles(ex,tr.get("symbol"),max(0,start-60)) if x["t"]>=start and x["t"]+60<=end]
    if rows:
        update_path(tr,d,rows,now); changed=True
    dx=diagnose(tr,d,str(d.get("status") or "").upper()=="COMPLETED")
    if d.get("diagnosis")!=dx:d["diagnosis"]=dx; changed=True
    return changed,str(d.get("status") or "").upper()!="COMPLETED"

def main():
    data=load(); trades=data.get("trades") or {}; now=now_ts()
    candidates=[]
    for tid,tr in trades.items():
        if not isinstance(tr,dict) or canon(tr.get("final_result")) not in TRACKED:continue
        closed=int(tr.get("closed_at") or 0)
        if closed<=0:continue
        d=tr.get("result_diagnostics")
        if isinstance(d,dict) and str(d.get("status") or "").upper()=="COMPLETED":continue
        if not isinstance(d,dict) and now-closed>RESTORE_HOURS*3600:continue
        candidates.append((0 if isinstance(d,dict) else 1,-closed,tid,tr))
    candidates.sort()
    if not candidates:
        if summary(data): save(data)
        print("Result diagnostics: takip edilecek yeni SL/BE yok."); return
    ex=exchange(); changed=False; active=0; initialized=0
    for _,__,tid,tr in candidates[:MAX_TRADES_PER_RUN]:
        before=isinstance(tr.get("result_diagnostics"),dict)
        try:
            ch,act=process(ex,tr,now); changed|=ch; active+=int(act); initialized+=int(ch and not before and isinstance(tr.get("result_diagnostics"),dict))
        except Exception as e: print(tid,"result diagnostics error:",e)
    changed|=summary(data)
    if changed and save(data):
        s=data.get("result_diagnostics_summary") or {}
        print("Result diagnostics saved | new:",initialized,"| active:",active,"| SL recovery:",s.get("sl_recovery_total",0),"| BE maybe early:",s.get("be_maybe_early_total",0))
    elif not changed: print("Result diagnostics: no changes.")

if __name__=="__main__":
    try: main()
    except Exception as e: print("Result diagnostics general error:",e)
