# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_1_2026_08_11`
- Mod: `READ_ONLY_MONITOR_NO_TELEGRAM_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-13T11:59:59+00:00
- Genel sağlık: 🟢 **GREEN**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 1 | 🟢 KORU | 0.15s |
| Scalp Radar | 🟢 GREEN | 0 | 🟠 SETUPLARI AYIR / GÖLGE TEST | 0.38s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.30s |
| Swing Radar | 🟢 GREEN | 3 | 🔴 CANLI İŞLEM KAYNAĞINI DURDUR | 0.55s |
| Ana Trend Pozisyon Radarı | 🟢 GREEN | 2 | ⚪ VERİ TOPLA | 1.49s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 2 | ⚪ VERİ TOPLA | 0.30s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 0.62s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 0.44s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 0.15s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 14.38s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 14.38s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.38s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.15s |

## Güvenlik

- `auto_apply = false`
- Telegram göndermez.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: Yok
