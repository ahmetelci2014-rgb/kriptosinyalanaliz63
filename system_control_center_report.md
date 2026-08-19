# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-19T05:47:20+00:00
- Genel sağlık: 🟡 **YELLOW**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟡 YELLOW | 3 | 🟢 KORU | 1.66s |
| Scalp Radar | 🟡 YELLOW | 0 | 🟡 İZLE | 2.10s |
| Pump/Dump Radar | 🟡 YELLOW | 0 | 🟢 KORU / İZLE | 2.29s |
| Swing Shadow V5 | 🟡 YELLOW | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 20.93s |
| Ana Trend Pozisyon Shadow V2 | 🟡 YELLOW | 4 | ⚪ VERİ TOPLA | 21.06s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 0 | 🟡 İZLE / DOĞRULA | 3.16s |
| Momentum Shadow V2 | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 3.06s |
| Range Cycle Shadow V4 | 🟢 GREEN | - | 🟡 GÖLGEDE TUT | 3.82s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 2.53s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 8.52s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 8.52s |
| New Listing Radar | 🟡 YELLOW | - | ⚪ KARAR YOK | 2.20s |
| TP Sonrası / Post Result Shadow | 🟡 YELLOW | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 1.66s |
| Büyük Hareket Fiyat Rotası | 🟢 GREEN | 0 | ⚪ KARAR YOK | 0.10s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: PREMIUM, SCALP, PUMP_DUMP, SWING_V4_SHADOW, POSITION_TREND, NEW_LISTING, POST_RESULT
