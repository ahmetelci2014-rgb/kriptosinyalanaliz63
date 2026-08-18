# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-18T20:41:25+00:00
- Genel sağlık: 🟡 **YELLOW**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 2 | 🟢 KORU | 0.10s |
| Scalp Radar | 🟢 GREEN | 0 | 🟡 İZLE | 0.26s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.38s |
| Swing Shadow V5 | 🟡 YELLOW | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 11.84s |
| Ana Trend Pozisyon Shadow V2 | 🟢 GREEN | 4 | ⚪ VERİ TOPLA | 11.96s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 1 | 🟡 İZLE / DOĞRULA | 0.87s |
| Momentum Shadow V2 | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 0.76s |
| Range Cycle Shadow V3 Arşiv | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 17.94s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 5.73s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 23.38s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 23.38s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.26s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.10s |
| Büyük Hareket Fiyat Rotası | 🟢 GREEN | 0 | ⚪ KARAR YOK | 0.51s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: SWING_V4_SHADOW
