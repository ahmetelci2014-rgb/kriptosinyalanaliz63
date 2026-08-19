import json, os, tempfile, unittest
import profitability_engine as p

class ProfitabilityEngineTests(unittest.TestCase):
    def test_cost_r_rises_when_stop_is_tighter(self):
        self.assertGreater(p.cost_r(100,99.5),p.cost_r(100,99))

    def test_before_entry_rejected(self):
        s={"direction":"LONG","entry":100,"sl":99,"tp1":100.55,"tp3":101.6}
        r=p.timing_gate(s,100)
        self.assertFalse(r["ok"]); self.assertEqual(r["reason"],"CONFIRMATION_NOT_STARTED")

    def test_confirmed_candidate_passes_timing(self):
        s={"direction":"LONG","entry":100,"sl":99,"tp1":100.55,"tp3":101.6}
        self.assertTrue(p.timing_gate(s,100.12)["ok"])

    def test_positive_history_promotes(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"ledger.json"); trades={}
            for i in range(30):
                trades[str(i)]={"status":"CLOSED","final_result":"TP3" if i<22 else "SL","r_result":1.1 if i<22 else -1,
                    "source":"15M_ENTRY","direction":"LONG","entry":100,"sl":99,"tp1":100.55,"tp3":101.6,
                    "tp1_progress_at_send_percent":20,"entry_distance_at_send_percent":0.12}
            with open(path,"w",encoding="utf-8") as f:json.dump({"trades":trades},f)
            self.assertTrue(p.premium_profile(path,"LONG")["live_allowed"])

    def test_negative_history_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"ledger.json"); trades={}
            for i in range(30):
                trades[str(i)]={"status":"CLOSED","final_result":"SL" if i<18 else "TP3","r_result":-1 if i<18 else 1.1,
                    "source":"15M_ENTRY","direction":"SHORT","entry":100,"sl":101,"tp1":99.45,"tp3":98.4,
                    "tp1_progress_at_send_percent":20,"entry_distance_at_send_percent":0.12}
            with open(path,"w",encoding="utf-8") as f:json.dump({"trades":trades},f)
            self.assertFalse(p.premium_profile(path,"SHORT")["live_allowed"])

if __name__=="__main__":unittest.main()
