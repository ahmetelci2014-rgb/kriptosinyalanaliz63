# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-16T10:40:33+00:00
- Genel sağlık: 🟡 **YELLOW**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 4 | 🟢 KORU | 0.10s |
| Scalp Radar | 🟢 GREEN | 0 | 🟡 İZLE | 0.44s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.08s |
| Swing Shadow V4 | 🟢 GREEN | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 1.94s |
| Ana Trend Pozisyon Radarı | 🟢 GREEN | 4 | ⚪ VERİ TOPLA | 2.08s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 0 | ⚪ VERİ TOPLA | 2.77s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 2.69s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 2.47s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 1.80s |
| Decision Engine | 🟡 YELLOW | - | ⚪ KARAR YOK | 37.37s |
| Prescription Engine | 🟡 YELLOW | - | ⚪ KARAR YOK | 37.37s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.54s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.10s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: DECISION_ENGINE, PRESCRIPTION_ENGINE
