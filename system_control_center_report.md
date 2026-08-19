# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-19T08:54:59+00:00
- Genel sağlık: 🟡 **YELLOW**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 5 | 🟢 KORU | 0.08s |
| Scalp Radar | 🟢 GREEN | 1 | 🟡 İZLE | 0.55s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.48s |
| Swing Shadow V5 | 🟡 YELLOW | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 24.06s |
| Ana Trend Pozisyon Shadow V2 | 🟡 YELLOW | 4 | ⚪ VERİ TOPLA | 24.19s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 1 | 🟡 İZLE / DOĞRULA | 0.86s |
| Momentum Shadow V2 | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 0.78s |
| Range Cycle Shadow V4 | 🟢 GREEN | - | 🟡 GÖLGEDE TUT | 6.95s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 5.65s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 11.65s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 11.65s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.59s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.08s |
| Büyük Hareket Fiyat Rotası | 🟢 GREEN | 0 | ⚪ KARAR YOK | 0.07s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: SWING_V4_SHADOW, POSITION_TREND
