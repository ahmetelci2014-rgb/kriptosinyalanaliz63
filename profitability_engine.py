from __future__ import annotations
import json, math, os, tempfile, time

VERSION="PROFIT_MODE_V2_COST_AWARE_2026_08_20"
REPORT_FILE="profit_mode_report.json"
FEE=float(os.getenv("PROFIT_FEE_RATE_PER_SIDE","0.0005"))
SLIP=float(os.getenv("PROFIT_SLIPPAGE_RATE_PER_SIDE","0.0001"))
FUND=float(os.getenv("PROFIT_FUNDING_RESERVE_RATE","0"))
MIN_PROG=float(os.getenv("PROFIT_MIN_TP1_PROGRESS_PERCENT","5"))
MAX_PROG=float(os.getenv("PROFIT_MAX_TP1_PROGRESS_PERCENT","40"))
MIN_DIST=float(os.getenv("PROFIT_MIN_ENTRY_DISTANCE_PERCENT","0.08"))
MAX_DIST=float(os.getenv("PROFIT_MAX_ENTRY_DISTANCE_PERCENT","0.25"))
MIN_TP1_BE_NET=float(os.getenv("PROFIT_MIN_TP1_BE_NET_R","0.05"))
MIN_SAMPLE=int(os.getenv("PROFIT_MIN_EVIDENCE_SAMPLE","20"))
MIN_AVG=float(os.getenv("PROFIT_MIN_AVG_NET_R","0.03"))
MIN_PF=float(os.getenv("PROFIT_MIN_PROFIT_FACTOR","1.10"))
MAX_STOP=float(os.getenv("PROFIT_MAX_STOP_RATE_PERCENT","32"))
COST_VERSION="TAKER_PLUS_SLIPPAGE_RESERVE_V1"

def sf(v,d=None):
    try:
        if v in (None,"","-"): return d
        x=float(v)
        return x if math.isfinite(x) else d
    except: return d

def load(path,default=None):
    if default is None: default={}
    try:
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except: return default

def save(path,data):
    folder=os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder,exist_ok=True); tmp=None
    try:
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=folder,delete=False,prefix=".profit.",suffix=".tmp") as f:
            tmp=f.name; json.dump(data,f,ensure_ascii=False,indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
        with open(tmp,"r",encoding="utf-8") as f: chk=json.load(f)
        if not isinstance(chk,dict): raise ValueError("root")
        os.replace(tmp,path); return True
    except Exception as e:
        print("Profit Mode V2 write:",type(e).__name__,e); return False
    finally:
        if tmp and os.path.exists(tmp):
            try: os.remove(tmp)
            except: pass

def cost_rate():
    return max(0.0,2*(FEE+SLIP)+FUND)

def risk_pct(entry,sl):
    e,s=sf(entry),sf(sl)
    if e is None or s is None or e<=0:return None
    x=abs(e-s)/e*100
    return x if x>0 else None

def target_r(entry,sl,target):
    e,s,t=sf(entry),sf(sl),sf(target)
    if None in (e,s,t):return None
    r=abs(e-s)
    return abs(t-e)/r if r>0 else None

def cost_r(entry,sl):
    r=risk_pct(entry,sl)
    return (cost_rate()*100/r) if r else None

def net_r(gross,entry,sl):
    g,c=sf(gross),cost_r(entry,sl)
    return None if g is None or c is None else g-c

def progress(signal,price):
    p,e,t=sf(price),sf(signal.get("entry")),sf(signal.get("tp1"))
    d=str(signal.get("direction") or "").upper()
    if None in (p,e,t): return None
    if d=="LONG":
        z=t-e
        return (p-e)/z*100 if z>0 else None
    if d=="SHORT":
        z=e-t
        return (e-p)/z*100 if z>0 else None
    return None

def distance(signal,price):
    p,e=sf(price),sf(signal.get("entry"))
    return abs(p-e)/e*100 if p is not None and e and e>0 else None

def cost_viability(signal):
    c=cost_r(signal.get("entry"),signal.get("sl"))
    t=target_r(signal.get("entry"),signal.get("sl"),signal.get("tp1"))
    if c is None or t is None:return {"ok":False,"reason":"COST_INPUT_MISSING"}
    n=.5*t-c
    return {"ok":n>=MIN_TP1_BE_NET,"reason":"OK" if n>=MIN_TP1_BE_NET else "TP1_BE_AFTER_COST_TOO_WEAK",
            "estimated_cost_r":round(c,4),"tp1_be_net_r":round(n,4),"risk_percent":round(risk_pct(signal.get("entry"),signal.get("sl")) or 0,4)}

def timing_gate(signal,price):
    p,d=progress(signal,price),distance(signal,price)
    base={"tp1_progress_percent":None if p is None else round(p,3),"entry_distance_percent":None if d is None else round(d,4)}
    if p is None or d is None:return {"ok":False,"reason":"TIMING_INPUT_MISSING",**base}
    if p<MIN_PROG:return {"ok":False,"reason":"CONFIRMATION_NOT_STARTED",**base}
    if p>MAX_PROG:return {"ok":False,"reason":"MOVE_TOO_ADVANCED",**base}
    if d<MIN_DIST:return {"ok":False,"reason":"PRICE_HAS_NOT_CONFIRMED_ENOUGH",**base}
    if d>MAX_DIST:return {"ok":False,"reason":"ENTRY_TOO_FAR",**base}
    c=cost_viability(signal)
    if not c["ok"]:return {"ok":False,"reason":c["reason"],"cost":c,**base}
    return {"ok":True,"reason":"TIMING_AND_COST_OK","cost":c,**base}

def metrics(rows):
    vals=[]; outs=[]
    for v,o in rows:
        x=sf(v)
        if x is not None:vals.append(x);outs.append(str(o).upper())
    pos=[x for x in vals if x>0]; neg=[x for x in vals if x<0]
    pf=sum(pos)/abs(sum(neg)) if neg else (999.0 if pos else 0.0)
    stops=sum(1 for o in outs if o in {"SL","STOP"})
    total=sum(vals); n=len(vals)
    return {"sample":n,"net_r_after_costs":round(total,4),"avg_net_r_after_costs":round(total/n,4) if n else None,
            "profit_factor_after_costs":round(pf,3),"stop_rate_percent":round(stops/n*100,2) if n else 0.0,
            "positive_rate_percent":round(len(pos)/n*100,2) if n else 0.0}

def passes(x):
    avg=sf(x.get("avg_net_r_after_costs"))
    pf=sf(x.get("profit_factor_after_costs"))
    stop=sf(x.get("stop_rate_percent"))
    return (
        x.get("sample",0)>=MIN_SAMPLE
        and avg is not None and avg>=MIN_AVG
        and pf is not None and pf>=MIN_PF
        and stop is not None and stop<=MAX_STOP
    )

def premium_profile(path="trade_ledger.json",direction=None):
    data=load(path,{}); trades=data.get("trades") or {}; good=[]
    for tr in trades.values() if isinstance(trades,dict) else []:
        if not isinstance(tr,dict) or str(tr.get("status")).upper()!="CLOSED" or sf(tr.get("r_result")) is None:continue
        if str(tr.get("source") or "").upper()!="15M_ENTRY":continue
        pr,di=sf(tr.get("tp1_progress_at_send_percent")),sf(tr.get("entry_distance_at_send_percent"))
        if pr is None or di is None or not(MIN_PROG<=pr<=MAX_PROG) or not(MIN_DIST<=abs(di)<=MAX_DIST):continue
        if not cost_viability(tr)["ok"]:continue
        good.append(tr)
    dn=str(direction or "").upper()
    sub=[t for t in good if str(t.get("direction") or "").upper()==dn] if dn in {"LONG","SHORT"} else []
    chosen=sub if len(sub)>=MIN_SAMPLE else good
    out=metrics((net_r(t.get("r_result"),t.get("entry"),t.get("sl")),t.get("final_result")) for t in chosen)
    out.update({"basis":"DIRECTION" if chosen is sub and sub else "ALL","direction":dn or "ALL","eligible_total":len(good),"direction_eligible":len(sub)})
    out["live_allowed"]=passes(out); return out

def radar_gross(r):
    o=str(r.get("trade_outcome") or "").upper()
    if o=="STOP":
        x=sf(r.get("trade_result_r")); return x if x is not None and x<0 else -1.0
    a=target_r(r.get("entry"),r.get("sl"),r.get("tp1"))
    b=target_r(r.get("entry"),r.get("sl"),r.get("tp3"))
    if a is None:return None
    if o=="BREAKEVEN":return .5*a
    if o=="TP3" and b is not None:return .5*a+.5*b
    return None

def radar_profile(path,setup=None):
    data=load(path,{}); recs=data.get("records") or []; rows=[]; key=str(setup or "").upper()
    for r in recs if isinstance(recs,list) else []:
        if not isinstance(r,dict) or str(r.get("stage") or "").upper()!="REAL_SIGNAL":continue
        setupv=str(r.get("setup") or r.get("setup_name") or r.get("source") or "").upper()
        if key and key not in setupv:continue
        g=radar_gross(r)
        if g is None:continue
        n=net_r(g,r.get("entry"),r.get("sl"))
        if n is not None:rows.append((n,r.get("trade_outcome")))
    out=metrics(rows); out.update({"basis":"RADAR_COST_ADJUSTED","setup":key or "ALL"}); out["live_allowed"]=passes(out); return out

def scalp_profile():return radar_profile("scalp_performance_ledger.json","TEPKI_SCALP")
def pump_profile():return radar_profile("pump_performance_ledger.json")

def enrich_premium(path="trade_ledger.json"):
    data=load(path,{}); trades=data.get("trades") or {}; changed=0
    if not isinstance(trades,dict):return 0
    for tr in trades.values():
        if not isinstance(tr,dict) or str(tr.get("status") or "").upper()!="CLOSED":continue
        g=sf(tr.get("r_result")); c=cost_r(tr.get("entry"),tr.get("sl"))
        if g is None or c is None:continue
        vals={"gross_r_before_costs":round(g,4),"estimated_execution_cost_r":round(c,4),"net_r_after_costs":round(g-c,4),"cost_model_version":COST_VERSION}
        if any(tr.get(k)!=v for k,v in vals.items()):tr.update(vals);changed+=1
    if changed:
        data["profit_mode_v2_last_enriched_at"]=int(time.time()); data["profit_mode_v2_cost_model"]=cost_meta(); save(path,data)
    return changed

def cost_meta():
    return {"version":COST_VERSION,"fee_rate_per_side":FEE,"slippage_reserve_rate_per_side":SLIP,"funding_reserve_rate":FUND,
            "round_trip_notional_cost_rate":round(cost_rate(),8)}

def report():
    x={"version":VERSION,"generated_at":int(time.time()),"cost_model":cost_meta(),
       "thresholds":{"min_progress":MIN_PROG,"max_progress":MAX_PROG,"min_distance":MIN_DIST,"max_distance":MAX_DIST,
                     "min_tp1_be_net_r":MIN_TP1_BE_NET,"min_sample":MIN_SAMPLE,"min_avg_net_r":MIN_AVG,"min_profit_factor":MIN_PF,"max_stop_rate":MAX_STOP},
       "premium":{"all":premium_profile(),"long":premium_profile(direction="LONG"),"short":premium_profile(direction="SHORT")},
       "scalp_tepki":scalp_profile(),"pump_dump":pump_profile()}
    save(REPORT_FILE,x);return x

class PremiumGate:
    def __init__(self,ledger="trade_ledger.json",rejects="profit_mode_rejections.json"):
        self.ledger=ledger;self.rejects=rejects;self.profiles={d:premium_profile(ledger,d) for d in ("LONG","SHORT")}
    def evaluate(self,signal,price):
        t=timing_gate(signal,price); e=self.profiles.get(str(signal.get("direction") or "").upper(),premium_profile(self.ledger))
        if not t["ok"]:return {"ok":False,"reason":t["reason"],"timing":t,"evidence":e}
        if not e["live_allowed"]:return {"ok":False,"reason":"HISTORICAL_NET_EDGE_NOT_PROVEN","timing":t,"evidence":e}
        return {"ok":True,"reason":"PROFIT_MODE_V2_ALLOWED","timing":t,"evidence":e}
    def reject(self,signal,price,result):
        data=load(self.rejects,{}); rows=data.get("records") or []
        if not isinstance(rows,list):rows=[]
        now=int(time.time()); sym=signal.get("symbol"); direc=signal.get("direction"); reason=result.get("reason")
        for old in reversed(rows[-100:]):
            if not isinstance(old,dict): continue
            if old.get("symbol")==sym and old.get("direction")==direc and old.get("reason")==reason and now-int(old.get("recorded_at") or 0)<900:
                return
        rows.append({"recorded_at":now,"symbol":sym,"direction":direc,"source":signal.get("source"),
                     "score":signal.get("score"),"entry":signal.get("entry"),"current_price":sf(price),"reason":reason,
                     "timing":result.get("timing"),"evidence":result.get("evidence")})
        save(self.rejects,{"version":VERSION,"records":rows[-1000:],"last_update":now})
