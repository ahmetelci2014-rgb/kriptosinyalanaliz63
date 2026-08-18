# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-18T23:36:35+00:00
- Genel sağlık: 🟡 **YELLOW**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 4 | 🟢 KORU | 0.01s |
| Scalp Radar | 🟢 GREEN | 0 | 🟡 İZLE | 0.36s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.08s |
| Swing Shadow V5 | 🟡 YELLOW | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 14.75s |
| Ana Trend Pozisyon Shadow V2 | 🟡 YELLOW | 4 | ⚪ VERİ TOPLA | 14.88s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 1 | 🟡 İZLE / DOĞRULA | 3.79s |
| Momentum Shadow V2 | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 3.68s |
| Range Cycle Shadow V4 | 🟢 GREEN | - | 🟡 GÖLGEDE TUT | 2.86s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 2.87s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 2.34s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 2.34s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.21s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.01s |
| Büyük Hareket Fiyat Rotası | 🟢 GREEN | 0 | ⚪ KARAR YOK | 0.69s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: SWING_V4_SHADOW, POSITION_TREND
