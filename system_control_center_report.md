# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-16T06:59:54+00:00
- Genel sağlık: 🟡 **YELLOW**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 2 | 🟢 KORU | 0.04s |
| Scalp Radar | 🟡 YELLOW | 0 | 🟡 İZLE | 23.74s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.05s |
| Swing Shadow V4 | 🟡 YELLOW | 1 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 23.90s |
| Ana Trend Pozisyon Radarı | 🟡 YELLOW | 3 | ⚪ VERİ TOPLA | 23.98s |
| Tüm Piyasa Keşif Radarı | 🟡 YELLOW | 2 | ⚪ VERİ TOPLA | 23.88s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 23.93s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 23.68s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 23.69s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 33.70s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 33.70s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 24.05s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.04s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: SCALP, SWING_V4_SHADOW, POSITION_TREND, ALL_MARKET
