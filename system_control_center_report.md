# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-17T17:45:27+00:00
- Genel sağlık: 🟢 **GREEN**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 2 | 🟢 KORU | 0.08s |
| Scalp Radar | 🟢 GREEN | 0 | 🟢 KORU | 0.46s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.47s |
| Swing Shadow V4 | 🟢 GREEN | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 3.10s |
| Ana Trend Pozisyon Radarı | 🟢 GREEN | 4 | ⚪ VERİ TOPLA | 3.20s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 5 | ⚪ VERİ TOPLA | 3.73s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 3.67s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 3.52s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 2.92s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 20.54s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 20.54s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.53s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.08s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: Yok
