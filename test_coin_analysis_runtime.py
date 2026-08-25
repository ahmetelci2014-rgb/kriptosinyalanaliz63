import coin_analysis_runtime as runtime


def test_modern_report_hides_leverage_when_no_candidate():
    raw = """
🔬 COIN DETAY ANALİZİ V2 — PREMIUM MİKROSKOP
Coin: POLUSDT
Market: POL/USDT:USDT
Fiyat: 0.107140
🧭 ÇOKLU ZAMAN DİLİMİ
• 1D: Yukarı | skor: 100.0
• 4H: 4H ana trend yukarı | ADX: 66.32 | RSI: 68.85
• 1H: 1H kararsız | ADX: 15.53 | RSI: 49.94
• 15M: Kararsız / geçiş | RSI: 52.71 | ADX: 19.53 | Hacim: 0.63x
• 5M: Kararsız / geçiş | Movement Start V2: V2 5M adayı yok
🌍 GENEL PİYASA / MARKET OUTLOOK
• 6s: GÜÇLÜ YUKARI (%82) | 24s: GÜÇLÜ YUKARI (%85) | LONG uygunluk 9.0 / SHORT 1.0
• Risk bayrakları: Yok
• Canlı legacy market guard: LONG=AÇIK | SHORT=KAPALI
📈 FUNDING / OPEN INTEREST — POLUSDT
• Funding: sağlıklı / aşırı kalabalık değil | funding +0.000050
• Open Interest: OI dengeli (%+0.57) | OI 4,910,655
🧬 ORDER-FLOW V3
• V2 PREP/ARMED/TRIGGER adayı yok; canlı V3 de order-flow sorgulamaz.
🔄 REVERSAL CAPTURE
• Yok
🚀 TREND CONTINUATION
• Aktif canlı aday yok
💎 PREMIUM KARAR
• Kaynak: YOK
• Premium skor: Aday oluşmadı
• Yakın stop cooldown: YOK
• Yakın kapanış cooldown: YOK / Reversal istisnası uygun olabilir
• Base giriş güvenliği: UYGUN DEĞİL — Premium adayı yok.
• Maliyet kontrolü: UYGUN DEĞİL | neden SIGNAL_MISSING
• Portfolio Risk: UYGUN
• Açık Premium risk: 1/6 riskli | 0 TP1 azaltılmış | toplam 1
• Duplicate: YOK
• Çekirdek kaldıraç: -
• Bağlamsal kaldıraç tavanı: 1x
📌 KARAR: BEKLE
Neden: Canlı Premium karar yollarının hiçbiri işlem adayı üretmedi.
"""
    report = runtime._modernize_report(raw)
    assert "💎 PREMIUM COIN MİKROSKOP" in report
    assert "🟡 KARAR  BEKLE" in report
    assert "Premium işlem adayı henüz oluşmadı." in report
    assert "Kaldıraç        •" not in report
    assert "Bağlamsal kaldıraç tavanı: 1x" not in report
    # Presentation spacing is cosmetic; assert the semantic 15M state/value.
    assert "15M 🟡 Kararsız / geçiş" in report
    assert "Hacim" in report and "0.63x" in report
    assert "Flow  Sorgulanmadı • 5M yapı adayı yok" in report


def test_modern_report_keeps_trade_plan_for_approved_signal():
    raw = """
Coin: TESTUSDT
Market: TEST/USDT:USDT
Fiyat: 1.0000
• 1D: Yukarı | skor: 80.0
• 4H: 4H ana trend yukarı | ADX: 30 | RSI: 60
• 1H: 1H alım onayı | ADX: 25 | RSI: 56
• 15M: Yukarı / toparlanma | RSI: 55 | ADX: 22 | Hacim: 1.40x
• 5M: Yukarı / toparlanma | Movement Start V2: LONG TRIGGER | skor 90/100
• 6s: YUKARI (%75) | 24s: YUKARI (%70)
• Risk bayrakları: Yok
• Canlı legacy market guard: LONG=AÇIK | SHORT=KAPALI
• Funding: sağlıklı / aşırı kalabalık değil | funding +0.000010
• Open Interest: OI dengeli (%+1.00) | OI 1,000,000
🧬 ORDER-FLOW V3
• Alıcı baskısı | LONG 80/100 | SHORT 20/100 | seçili yön teyidi: EVET
🔄 REVERSAL CAPTURE
• Yok
🚀 TREND CONTINUATION
• Aktif canlı aday yok
• Kaynak: Klasik Premium MTF
• Premium skor: 95/100
• Yakın stop cooldown: YOK
• Yakın kapanış cooldown: YOK
• Base giriş güvenliği: UYGUN — OK
• Maliyet kontrolü: UYGUN | neden OK
• Portfolio Risk: UYGUN
• Açık Premium risk: 1/6 riskli | 0 TP1 azaltılmış | toplam 1
• Duplicate: YOK
• Çekirdek kaldıraç: 2x
• Bağlamsal kaldıraç tavanı: 2x
📌 KARAR: LONG
Neden: Canlı Premium kapıları geçti.
✅ PREMIUM ONAYLI İŞLEM PLANI
Yön: LONG
Giriş: 1.0000
TP1: 1.0100
TP2: 1.0200
TP3: 1.0300
SL: 0.9900
Stop Mesafesi: %1.00
R/R: 0.5 / 1.0 / 1.5
Çekirdek Kaldıraç: 2x
Bağlamsal Kaldıraç Tavanı: 2x
"""
    report = runtime._modernize_report(raw)
    assert "🟢 KARAR  LONG" in report
    assert "✅ İŞLEM PLANI" in report
    assert "Giriş  •  1.0000" in report
    assert "TP3  •  1.0300" in report
    assert "Kaldıraç        • 2x | tavan 2x" in report
