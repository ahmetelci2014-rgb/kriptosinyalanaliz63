# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-17T11:41:08+00:00
- Genel sağlık: 🟡 **YELLOW**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 3 | 🟢 KORU | 0.16s |
| Scalp Radar | 🟢 GREEN | 1 | 🟢 KORU | 0.45s |
| Pump/Dump Radar | 🟢 GREEN | 1 | 🟢 KORU / İZLE | 0.03s |
| Swing Shadow V4 | 🟡 YELLOW | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 8.51s |
| Ana Trend Pozisyon Radarı | 🟡 YELLOW | 4 | ⚪ VERİ TOPLA | 8.60s |
| Tüm Piyasa Keşif Radarı | 🟡 YELLOW | 0 | ⚪ VERİ TOPLA | 9.01s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 8.90s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 8.88s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 8.28s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 14.47s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 14.47s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.02s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.11s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: SWING_V4_SHADOW, POSITION_TREND, ALL_MARKET
