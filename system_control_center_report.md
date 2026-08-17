# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-17T13:53:28+00:00
- Genel sağlık: 🟡 **YELLOW**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 4 | 🟢 KORU | 0.05s |
| Scalp Radar | 🟢 GREEN | 0 | 🟢 KORU | 0.47s |
| Pump/Dump Radar | 🟢 GREEN | 1 | 🟢 KORU / İZLE | 0.01s |
| Swing Shadow V4 | 🟡 YELLOW | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 10.72s |
| Ana Trend Pozisyon Radarı | 🟡 YELLOW | 4 | ⚪ VERİ TOPLA | 10.81s |
| Tüm Piyasa Keşif Radarı | 🟡 YELLOW | 0 | ⚪ VERİ TOPLA | 11.22s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 11.11s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 11.08s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 10.48s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 16.68s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 16.68s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.02s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.05s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: SWING_V4_SHADOW, POSITION_TREND, ALL_MARKET
