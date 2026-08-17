# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-17T23:37:04+00:00
- Genel sağlık: 🟢 **GREEN**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 6 | 🟢 KORU | 0.02s |
| Scalp Radar | 🟢 GREEN | 0 | 🟡 İZLE | 0.38s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.04s |
| Swing Shadow V4 | 🟢 GREEN | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 2.96s |
| Ana Trend Pozisyon Radarı | 🟢 GREEN | 4 | ⚪ VERİ TOPLA | 3.07s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 3 | ⚪ VERİ TOPLA | 3.76s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 3.65s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 3.44s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 2.79s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 2.31s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 2.31s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.07s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.02s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: Yok
