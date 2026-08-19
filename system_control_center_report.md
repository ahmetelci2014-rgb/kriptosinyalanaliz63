# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_4_GLOBAL_JSON_GUARD_2026_08_14`
- Mod: `READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-19T03:11:39+00:00
- Genel sağlık: 🟡 **YELLOW**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 5 | 🟢 KORU | 0.10s |
| Scalp Radar | 🟢 GREEN | 0 | 🟡 İZLE | 0.22s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.62s |
| Swing Shadow V5 | 🟡 YELLOW | 0 | ⚪ SWING V4 GÖLGE VERİ TOPLA | 18.34s |
| Ana Trend Pozisyon Shadow V2 | 🟡 YELLOW | 4 | ⚪ VERİ TOPLA | 18.47s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 0 | 🟡 İZLE / DOĞRULA | 0.56s |
| Momentum Shadow V2 | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 0.46s |
| Range Cycle Shadow V4 | 🟢 GREEN | - | 🟡 GÖLGEDE TUT | 1.23s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 6.45s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 5.93s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 5.93s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.39s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.10s |
| Büyük Hareket Fiyat Rotası | 🟢 GREEN | 0 | ⚪ KARAR YOK | 1.10s |
| JSON Depolama Koruması | 🟢 GREEN | - | ⚪ TEKNİK KORUMA | - |

## Güvenlik

- `auto_apply = false`
- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: SWING_V4_SHADOW, POSITION_TREND
