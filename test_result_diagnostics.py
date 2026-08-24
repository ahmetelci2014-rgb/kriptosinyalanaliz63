import result_diagnostics as rd


def trade(result="SL", **kw):
    x={"symbol":"TESTUSDT","direction":"LONG","entry":100.0,"sl":99.0,"tp1":100.55,"tp2":101.05,"tp3":101.6,
       "exit_price":99.0 if result=="SL" else 100.0,"closed_at":1_000_000,"final_result":result,
       "tp1_progress_at_send_percent":8.0,"entry_distance_at_send_percent":0.10}
    x.update(kw); return x


def test_sl_tracks_targets():
    tr=trade("SL"); d=rd.new_diag(tr,"SL",tr["closed_at"])
    assert d["reference_label"]=="SL_EXIT"
    assert set(d["watched_levels"])=={"TP1","TP2","TP3"}


def test_sl_recovery_diagnosis():
    tr=trade("SL"); d=rd.new_diag(tr,"SL",tr["closed_at"])
    d["max_favorable_r"]=1.2; d["max_adverse_r"]=0.2
    d["reached_levels"]={"TP2":{"minutes_after_close":60}}
    dx=rd.diagnose(tr,d,True)
    assert dx["code"]=="SL_SONRASI_GUCLU_TOPARLANMA"
    assert dx["likely_cause"]=="ERKEN_GIRIS_OLASILIGI"
    assert dx["confidence"]=="HIGH"


def test_sl_wrong_direction_diagnosis():
    tr=trade("SL",tp1_progress_at_send_percent=20); d=rd.new_diag(tr,"SL",tr["closed_at"])
    d["max_favorable_r"]=0.1; d["max_adverse_r"]=1.2
    dx=rd.diagnose(tr,d,True)
    assert dx["code"]=="SL_SONRASI_TERS_YON_DEVAMI"


def test_be_after_tp3_is_maybe_early():
    tr=trade("TP1_SONRASI_BE",exit_price=100); d=rd.new_diag(tr,"TP1_SONRASI_BE",tr["closed_at"])
    d["reached_levels"]={"TP3":{"minutes_after_close":90}}
    dx=rd.diagnose(tr,d,True)
    assert dx["code"]=="BE_SONRASI_TP3"
    assert dx["likely_cause"]=="BE_KORUMASI_ERKEN_OLABILIR"


def test_be_reversal_is_correct_protection():
    tr=trade("BE",exit_price=100); d=rd.new_diag(tr,"BE",tr["closed_at"])
    d["max_favorable_r"]=0.1; d["max_adverse_r"]=0.8
    dx=rd.diagnose(tr,d,True)
    assert dx["code"]=="BE_KORUMASI_DOGRU"


def test_summary_is_stable(monkeypatch):
    monkeypatch.setattr(rd,"now_ts",lambda:123)
    a=trade("SL"); da=rd.new_diag(a,"SL",a["closed_at"]); da["status"]="COMPLETED"; da["diagnosis"]={"code":"SL_SONRASI_TOPARLANMA","likely_cause":"GIRIS_STOP_ZAMANLAMASI"}; a["result_diagnostics"]=da
    b=trade("BE",exit_price=100); db=rd.new_diag(b,"BE",b["closed_at"]); db["status"]="COMPLETED"; db["diagnosis"]={"code":"BE_SONRASI_TP3","likely_cause":"BE_KORUMASI_ERKEN_OLABILIR"}; b["result_diagnostics"]=db
    data={"trades":{"a":a,"b":b}}
    assert rd.summary(data) is True
    assert data["result_diagnostics_summary"]["sl_recovery_total"]==1
    assert data["result_diagnostics_summary"]["be_maybe_early_total"]==1
    assert rd.summary(data) is False
