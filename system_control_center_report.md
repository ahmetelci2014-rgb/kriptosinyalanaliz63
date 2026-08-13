# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_1_2026_08_11`
- Mod: `READ_ONLY_MONITOR_NO_TELEGRAM_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-13T23:58:50+00:00
- Genel sağlık: 🟢 **GREEN**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 5 | 🟢 KORU | 0.15s |
| Scalp Radar | 🟢 GREEN | 0 | 🟡 İZLE | 0.75s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.18s |
| Swing Radar | 🟢 GREEN | 3 | 🔴 CANLI İŞLEM KAYNAĞINI DURDUR | 0.97s |
| Ana Trend Pozisyon Radarı | 🟢 GREEN | 4 | ⚪ VERİ TOPLA | 0.88s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 0 | ⚪ VERİ TOPLA | 0.27s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 0.09s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 0.04s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 0.56s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 2.37s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 2.37s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.25s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.15s |

## Güvenlik

- `auto_apply = false`
- Telegram göndermez.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: Yok
