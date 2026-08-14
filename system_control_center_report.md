# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_1_2026_08_11`
- Mod: `READ_ONLY_MONITOR_NO_TELEGRAM_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-14T05:02:45+00:00
- Genel sağlık: 🟢 **GREEN**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 2 | 🟢 KORU | 0.22s |
| Scalp Radar | 🟢 GREEN | 0 | 🟡 İZLE | 1.34s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 1.75s |
| Swing Radar | 🟢 GREEN | 2 | 🔴 CANLI İŞLEM KAYNAĞINI DURDUR | 2.30s |
| Ana Trend Pozisyon Radarı | 🟢 GREEN | 3 | ⚪ VERİ TOPLA | 2.22s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 1 | ⚪ VERİ TOPLA | 0.04s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 0.02s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 1.52s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 0.22s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 7.43s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 7.43s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 1.74s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.22s |

## Güvenlik

- `auto_apply = false`
- Telegram göndermez.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: Yok
